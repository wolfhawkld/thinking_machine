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

    def test_next_one_world_gate_seed_is_reserved_before_results(self) -> None:
        gate = self.load_json("configs/development-gate.json")
        registry = self.load_json("configs/development-seed-registry.json")

        self.assertEqual(gate["worlds"], [{"seed": 2000, "depth": 3}])
        reserved = next(
            record for record in registry["records"] if record.get("seed") == 2000
        )
        self.assertEqual(reserved["status"], "reserved-operational-calibration")
        self.assertEqual(reserved["uses"], ["next-one-world-development-gate"])
        retired = next(
            record for record in registry["records"] if record.get("seed") == 1000
        )
        self.assertIn("attempt-B-interface-calibration", retired["uses"])

    def test_confirmatory_config_is_an_unfrozen_empty_template(self) -> None:
        config = self.load_json("configs/confirmatory.template.json")

        self.assertEqual(config["status"], "template-not-frozen")
        self.assertEqual(config["worlds"], [])
        self.assertEqual(config["episode"]["max_output_tokens"], 256)
        self.assertEqual(config["target_world_count"], 40)
        self.assertIsNone(config["model"]["name"])


if __name__ == "__main__":
    unittest.main()
