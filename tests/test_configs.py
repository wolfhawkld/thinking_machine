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

    def test_v3_development_template_is_frozen_except_model_bindings(self) -> None:
        config = self.load_json("configs/v3-development.template.json")
        registry = self.load_json("configs/development-seed-registry.json")

        self.assertEqual(config["protocol_version"], 3)
        self.assertFalse(config["independence"]["continues_staged_v2_s3"])
        self.assertEqual(
            [model["expected_model_family"] for model in config["model_strata"]],
            ["DeepSeek v4", "MiniMax M3"],
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
