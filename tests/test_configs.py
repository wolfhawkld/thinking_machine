import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExperimentConfigTests(unittest.TestCase):
    def load_json(self, relative_path: str) -> dict:
        with (ROOT / relative_path).open(encoding="utf-8") as handle:
            return json.load(handle)

    def test_pilot_budget_and_arms_match_specification(self) -> None:
        config = self.load_json("configs/pilot.json")

        self.assertEqual(config["episode"]["rounds"], 5)
        self.assertEqual(config["episode"]["candidates_per_round"], 4)
        self.assertEqual(config["episode"]["max_output_tokens"], 256)
        self.assertEqual(set(config["arms"]), {"L", "M", "H", "A", "C", "MTX", "E"})
        self.assertEqual(len(config["arms"]["A"]["temperatures"]), 5)
        self.assertEqual(len(config["arms"]["C"]["temperatures"]), 5)
        self.assertEqual(len(config["arms"]["MTX"]["temperatures"]), 4)

    def test_pilot_worlds_are_unique_and_development_only(self) -> None:
        config = self.load_json("configs/pilot.json")
        seeds = [world["seed"] for world in config["worlds"]]

        self.assertEqual(config["status"], "development-only")
        self.assertEqual(len(seeds), 8)
        self.assertEqual(len(seeds), len(set(seeds)))
        self.assertTrue(all(world["depth"] in {3, 4, 5} for world in config["worlds"]))
        self.assertNotIn(1000, seeds)
        self.assertIn(1008, seeds)

        registry = self.load_json("configs/development-seed-registry.json")
        self.assertEqual(registry["schema_version"], 1)
        self.assertEqual(len(registry["seeds"]), len(set(registry["seeds"])))
        self.assertTrue(set(seeds).issubset(registry["seeds"]))
        self.assertIn(1000, registry["seeds"])
        self.assertIn(2000, registry["seeds"])

    def test_gate_seed_is_reserved_for_operational_calibration(self) -> None:
        gate = self.load_json("configs/development-gate.json")
        registry = self.load_json("configs/development-seed-registry.json")

        self.assertEqual(gate["worlds"], [{"seed": 2000, "depth": 3}])
        reserved = next(
            record for record in registry["records"] if record.get("seed") == 2000
        )
        self.assertEqual(reserved["status"], "reserved-operational-calibration")
        self.assertEqual(
            reserved["uses"],
            [
                "volcengine-gate-c",
                "v3-two-model-validity-and-manipulation-gate",
            ],
        )
        retired = next(
            record for record in registry["records"] if record.get("seed") == 1000
        )
        self.assertIn("attempt-B-interface-calibration", retired["uses"])

    def test_strong_k4_feasibility_stream_is_frozen_and_reserved(self) -> None:
        config = self.load_json("configs/spark-strong-k4-feasibility-v2.json")
        registry = self.load_json("configs/development-seed-registry.json")
        stream = config["candidate_stream"]
        namespace = stream["world_seed_namespace"]
        count = stream["candidate_world_count"]
        expected = [
            int.from_bytes(
                hashlib.sha256(f"{namespace}:{index}".encode("ascii")).digest()[:8],
                "big",
            )
            & ((1 << 63) - 1)
            for index in range(count)
        ]

        self.assertEqual(stream["candidate_index_start"], 0)
        self.assertEqual(count, 1024)
        self.assertEqual(stream["stage_world_count"], 64)
        self.assertIs(stream["fixed_full_scan_required"], True)
        self.assertEqual(len(expected), len(set(expected)))
        self.assertEqual(registry["seeds"][-count:], expected)
        self.assertFalse(set(registry["seeds"][:-count]).intersection(expected))
        digest = hashlib.sha256(
            json.dumps(expected, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(digest, stream["candidate_seed_vector_sha256"])
        self.assertEqual(
            config["endpoint"]["minimum_unique_control_behaviors"], 3
        )
        target = config["private_target_and_public_motif_namespaces"]
        self.assertIn("full 32-byte digest", target["target_seed_rule"])
        self.assertIn("exactly once", target["target_selection_rule"])
        cohort = config["balanced_cohort"]
        self.assertEqual(cohort["target_worlds_per_stratum"], 8)
        self.assertEqual(cohort["target_total_worlds"], 4 * 8)
        self.assertEqual(cohort["fallback_worlds_per_stratum"], 6)
        self.assertEqual(cohort["fallback_total_worlds"], 4 * 6)
        self.assertEqual(cohort["full_classification"], "full_32_balanced_feasible")
        self.assertEqual(
            cohort["failure_classification"],
            "balanced_strong_K4_benchmark_not_feasible_under_cap",
        )
        self.assertIn("capacity 1", cohort["matching_graph"])
        self.assertEqual(config["artifact_contract"]["provider_calls_made"], 0)
        self.assertIs(config["artifact_contract"]["model_outputs_read"], False)
        reservation = registry["records"][-1]
        self.assertEqual(reservation["candidate_indices"], {"start": 0, "count": 1024})
        self.assertEqual(reservation["candidate_seed_vector_sha256"], digest)
        retired = registry["records"][-2]
        self.assertEqual(retired["status"], "retired-pre-plan-implementation-smoke")
        self.assertIn("candidate index 0", retired["retirement_reason"])

    def test_utilization_feasibility_stream_is_fresh_reserved_and_offline(self) -> None:
        config = self.load_json(
            "configs/spark-strong-k4-utilization-feasibility-v2.json"
        )
        reservation_path = (
            ROOT / "configs/spark-strong-k4-utilization-feasibility-v2-seeds.json"
        )
        reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
        registry_path = ROOT / "configs/development-seed-registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        stream = config["candidate_stream"]
        namespace = stream["world_seed_namespace"]
        count = stream["candidate_world_count_cap"]
        expected = [
            int.from_bytes(
                hashlib.sha256(f"{namespace}:{index}".encode("ascii")).digest()[:8],
                "big",
            )
            & ((1 << 63) - 1)
            for index in range(count)
        ]

        self.assertEqual(config["protocol_id"], reservation["protocol_id"])
        self.assertEqual(count, 1024)
        self.assertEqual(stream["stage_world_count"], 8)
        self.assertIs(stream["fixed_full_scan_required"], True)
        self.assertIs(stream["outcome_dependent_early_stop_allowed"], False)
        self.assertEqual(reservation["seeds"], expected)
        self.assertEqual(len(expected), len(set(expected)))
        self.assertFalse(set(registry["seeds"]).intersection(expected))
        vector_sha = hashlib.sha256(
            json.dumps(expected, separators=(",", ":")).encode("ascii")
        ).hexdigest()
        self.assertEqual(vector_sha, stream["candidate_seed_vector_sha256"])
        self.assertEqual(vector_sha, reservation["candidate_seed_vector_sha256"])
        retired = reservation["retired_namespaces"]
        self.assertEqual(len(retired), 1)
        self.assertEqual(
            retired[0]["status"],
            "retired_pre_plan_implementation_smoke",
        )
        self.assertIs(retired[0]["entire_namespace_retired"], True)
        self.assertEqual(
            retired[0]["materialized_before_reviewed_plan"],
            [
                {
                    "candidate_index": 0,
                    "world_seed": 3092638349656038141,
                    "target_materialization_count": 1,
                    "compressor_run": True,
                    "context_count": 105,
                    "raw_action_evaluation_count": 1050,
                    "artifact_persisted": False,
                    "model_or_provider_calls": 0,
                }
            ],
        )
        self.assertEqual(
            reservation["active_vector_targets_materialized_before_reviewed_plan"],
            0,
        )
        self.assertEqual(
            hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            config["seed_reservation"]["base_registry_file_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(registry_path.read_bytes()).hexdigest(),
            reservation["base_registry"]["file_sha256"],
        )
        self.assertEqual(config["context_universe"]["candidates_per_world"], 105)
        self.assertEqual(
            sum(config["context_universe"]["motif_counts_by_stratum"].values()),
            105,
        )
        self.assertEqual(config["action_universe"]["raw_actions_per_context"], 10)
        self.assertEqual(
            config["utilization_pair_tiers"]["evaluation_order"],
            [
                "strict_unique_nonconstant_switch",
                "degraded_two_choice_disjoint_switch",
            ],
        )
        self.assertIs(
            config["target_free_plan_barrier"]["target_materialized"], False
        )
        self.assertIs(config["artifact_contract"]["evidence"], False)
        self.assertIs(config["artifact_contract"]["confirmatory"], False)
        self.assertEqual(config["artifact_contract"]["provider_calls_made"], 0)
        self.assertIs(config["artifact_contract"]["model_outputs_read"], False)

    def test_utilization_power_protocol_is_paired_exact_and_offline(self) -> None:
        config = self.load_json("configs/spark-strong-k4-utilization-power-v1.json")

        self.assertEqual(
            config["protocol_id"],
            "spark-strong-k4-utilization-power-v1",
        )
        self.assertEqual(
            [
                (item["tier_id"], item["balanced_q"], item["world_count"])
                for item in config["candidate_designs"]
            ],
            [
                ("strict_unique_nonconstant_switch", 4, 16),
                ("strict_unique_nonconstant_switch", 6, 24),
                ("degraded_two_choice_disjoint_switch", 8, 32),
            ],
        )
        self.assertTrue(
            all(
                "paired net evidence" in item["claim_limit"]
                for item in config["candidate_designs"]
            )
        )
        self.assertIn(
            "never unique-action switching",
            config["candidate_designs"][2]["claim_limit"],
        )
        primary = config["primary_estimand"]
        self.assertEqual(
            primary["primary_test"],
            "one-sided exact sign test conditional on non-tie worlds",
        )
        self.assertIs(primary["calls_are_not_independent_units"], True)
        self.assertIs(primary["routes_may_not_be_pooled_as_independent_worlds"], True)
        self.assertIs(primary["tier_mixing_allowed"], False)
        self.assertIn("jointly exchangeable", primary["null_exchangeability_assumption"])
        self.assertIn("hard balance alone", primary["choice_bias_control"])
        self.assertEqual(
            config["route_family"]["family_alpha"],
            {"numerator": 1, "denominator": 20},
        )
        self.assertEqual(
            config["route_family"]["conservative_design_alpha"],
            {"numerator": 1, "denominator": 60},
        )
        self.assertEqual(
            config["power_model"]["target_power"],
            {"numerator": 9, "denominator": 10},
        )
        self.assertEqual(
            config["power_model"]["frozen_sesoi"]["p_favorable"],
            {"numerator": 3, "denominator": 5},
        )
        self.assertEqual(
            config["power_model"]["frozen_sesoi"]["p_adverse"],
            {"numerator": 1, "denominator": 10},
        )
        self.assertIs(config["power_model"]["stratum_heterogeneity_modeled"], False)
        self.assertIn("prospective sensitivity model", config["power_model"]["working_model"])
        secondary = config["secondary_endpoints"]
        self.assertIs(secondary["uniform_calibration_is_primary_inference"], False)
        self.assertIs(
            secondary[
                "route_specific_choice_baseline_power_identifiable_from_safe_manifest"
            ],
            False,
        )
        artifact = config["artifact_contract"]
        self.assertIs(artifact["evidence"], False)
        self.assertIs(artifact["confirmatory"], False)
        self.assertIs(artifact["private_geometry_read"], False)
        self.assertIs(artifact["final_benchmark_minted"], False)
        self.assertEqual(artifact["provider_calls_made"], 0)
        self.assertIs(artifact["model_outputs_read"], False)

    def test_strong_k4_fair_choice_protocol_is_symmetric_and_masked(self) -> None:
        config = self.load_json("configs/spark-strong-k4-fair-choice-v1.json")

        self.assertEqual(config["protocol_id"], "spark-strong-k4-fair-choice-v1")
        sealed = config["sealed_input"]
        for field in (
            "file_sha256",
            "plan_file_sha256",
            "config_file_sha256",
            "plan_sha256",
            "scan_sha256",
            "historical_source_manifest_sha256",
        ):
            self.assertEqual(len(sealed[field]), 64)
        self.assertEqual(
            hashlib.sha256((ROOT / sealed["relative_path"]).read_bytes()).hexdigest(),
            sealed["file_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(
                (ROOT / sealed["plan_relative_path"]).read_bytes()
            ).hexdigest(),
            sealed["plan_file_sha256"],
        )
        self.assertEqual(
            hashlib.sha256(
                (ROOT / "configs/spark-strong-k4-feasibility-v2.json").read_bytes()
            ).hexdigest(),
            sealed["config_file_sha256"],
        )
        self.assertEqual(len(sealed["artifact_commit"]), 40)
        self.assertEqual(len(sealed["source_freeze_commit"]), 40)

        self.assertEqual(config["paired_design"]["pairs"], 32)
        self.assertEqual(config["paired_design"]["prompts_per_pair"], 2)
        self.assertEqual(32 * 2, config["planned_routes"]["logical_calls_per_route"])
        self.assertIn("candidate_index ascending", config["paired_design"]["pair_ordinal_rule"])
        self.assertIn("even", config["paired_design"]["condition_order_rule"])
        self.assertIn("phase 1", config["paired_design"]["public_task_sequence"])
        self.assertEqual(
            config["paired_design"]["only_pair_difference"],
            "rendered motif expression",
        )
        self.assertTrue(
            config["matched_sham"]["score_all_ten_sham_actions_symmetrically"]
        )
        self.assertIn(
            "K2, K3 or K4_full_pool",
            config["matched_sham"]["forbidden_selection_inputs"],
        )
        self.assertEqual(
            config["matched_sham"]["expected_pre_model_audit"]
            ["minimum_hamming_distance_histogram"],
            {"0": 20, "1": 6, "2": 4, "3": 2},
        )
        self.assertIn("canonical JSON", config["matched_sham"]["alias_representative_rule"])
        self.assertIn("Hamming distance", config["matched_sham"]["behavior_tie_break_rule"])
        self.assertFalse(config["choice_masking"]["answer_example_in_prompt"])
        self.assertIn("three or four", config["choice_masking"]["global_position_balance"])
        self.assertEqual(
            len(config["target_blind_structural_baselines"]["deterministic_policy_ids"]),
            24,
        )
        baseline_audit = config["target_blind_structural_baselines"][
            "expected_pre_model_audit"
        ]
        self.assertEqual(baseline_audit["B_star_factual_K4_count"], 19)
        self.assertEqual(baseline_audit["best_fixed_semantic_factual_K4_count"], 14)
        self.assertEqual(
            baseline_audit["uniform_exact_upper_tail_critical_count_at_alpha_0_05"],
            10,
        )
        self.assertTrue(
            config["provider_facing_public_manifest"]
            ["private_scoring_key_is_separate_file"]
        )
        self.assertFalse(
            config["provider_facing_public_manifest"]
            ["generation_may_read_private_key"]
        )
        self.assertIn(
            "private-design commitment",
            config["provider_facing_public_manifest"]["cross_binding"],
        )
        self.assertFalse(config["response_contract"]["prose_or_extra_keys_valid"])
        self.assertFalse(
            config["response_contract"]["bare_choice_id_without_the_JSON_object_valid"]
        )
        self.assertEqual(config["analysis"]["usefulness_endpoint"]["name"], "K2")
        self.assertEqual(config["analysis"]["alpha"], 0.05)
        self.assertIn("zero discordances gives p=1", config["analysis"]["exact_mcnemar_formula"])
        self.assertEqual(
            config["analysis"]["route_gates"]["p_route"],
            "maximum of paired raw p and shortcut raw p",
        )
        self.assertEqual(
            config["analysis"]["route_gates"]["holm_adjusted_p_route_at_most"],
            0.05,
        )
        self.assertEqual(config["planned_routes"]["route_count"], 3)
        self.assertEqual(
            config["planned_routes"]["route_ids"],
            ["deepseek-flash", "deepseek-pro", "glm-5.2"],
        )
        self.assertEqual(config["planned_routes"]["total_logical_calls"], 192)
        self.assertFalse(
            config["new_format_canary"]["old_action_grammar_canary_is_sufficient"]
        )
        barrier = config["later_sealed_plan_barrier"]
        self.assertTrue(barrier["must_bind_current_source_manifest_sha256"])
        self.assertTrue(barrier["must_verify_public_private_64_task_bijection"])
        self.assertEqual(barrier["live_runner_may_read"], "public manifest only")
        self.assertFalse(
            barrier["formal_plan_or_science_artifact_generated_during_this_code_change"]
        )

    def test_v3_development_template_is_frozen_except_model_bindings(self) -> None:
        config = self.load_json("configs/v3-development.template.json")
        registry = self.load_json("configs/development-seed-registry.json")

        self.assertEqual(config["protocol_version"], 3)
        self.assertFalse(config["independence"]["continues_staged_v2_s3"])
        self.assertEqual(
            [model["expected_model_family"] for model in config["model_strata"]],
            ["DeepSeek v4", "Kimi K3"],
        )
        for model in config["model_strata"]:
            self.assertIsNone(model["provider"])
            self.assertIsNone(model["name"])
            self.assertIsNone(model["snapshot"])
            self.assertTrue(model["pre_execution_freeze_required"])

        worlds = config["worlds"]
        seeds = [world["seed"] for world in worlds]
        self.assertEqual(seeds, list(range(2001, 2013)))
        self.assertTrue(set(seeds).issubset(registry["seeds"]))
        self.assertEqual(
            {depth: sum(world["depth"] == depth for world in worlds) for depth in (3, 4, 5)},
            {3: 4, 4: 4, 5: 4},
        )
        self.assertEqual(config["gate_world"]["seed"], 2000)
        self.assertEqual(set(config["arms"]), {"L", "H", "C", "E2"})
        self.assertEqual(config["primary_reference_arm"], "C")
        self.assertEqual(config["execution"]["main_logical_calls"], 1920)
        self.assertEqual(config["execution"]["gate_logical_calls"], 160)

        e2 = config["arms"]["E2"]
        self.assertEqual(e2["controller_version"], "validity-novelty-v2")
        self.assertEqual(e2["minimum_valid_candidates"], 3)
        self.assertEqual(e2["minimum_useful_new_behaviors"], 1)
        self.assertAlmostEqual(e2["useful_novelty_score_tolerance"], 1 / 12)
        self.assertEqual(
            config["compatibility_screen"]["minimum_overall_search_valid_rate"],
            0.95,
        )
        self.assertEqual(
            config["compatibility_screen"]["minimum_per_arm_search_valid_rate"],
            0.90,
        )
        self.assertTrue(
            config["development_diagnostics"][
                "performance_classification_still_reported"
            ]
        )

        terminal = config["terminal_endpoint"]
        self.assertTrue(terminal["primary_endpoint_failure"])
        self.assertEqual(terminal["primary_analysis_score"], 0.0)
        self.assertFalse(terminal["zero_is_observed_accuracy"])
        execution = config["execution"]
        self.assertEqual(
            execution["accepted_attempt_semantics"],
            "first_durably_recorded_http_success",
        )
        self.assertFalse(execution["content_retry_supported"])
        self.assertEqual(execution["content_retry_count_required"], 0)
        self.assertEqual(config["statistical_analysis"]["primary_unit"], "world")
        self.assertEqual(
            config["statistical_analysis"]["bootstrap"]["replicates"],
            100000,
        )

    def test_confirmatory_config_is_an_unfrozen_empty_template(self) -> None:
        config = self.load_json("configs/confirmatory.template.json")

        self.assertEqual(config["status"], "template-not-frozen")
        self.assertEqual(config["worlds"], [])
        self.assertEqual(config["episode"]["max_output_tokens"], 256)
        self.assertEqual(config["target_world_count"], 40)
        self.assertIsNone(config["model"]["name"])


if __name__ == "__main__":
    unittest.main()
