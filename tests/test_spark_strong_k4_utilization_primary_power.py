from __future__ import annotations

import copy
import hashlib
import inspect
import json
from fractions import Fraction
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import spark_strong_k4_utilization_primary_power as power
from src.provenance import PROJECT_ROOT


CONFIG_PATH = PROJECT_ROOT / "configs" / "spark-strong-k4-utilization-primary-route-power-v1.json"
MANIFEST_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-strong-k4-utilization-feasibility-v2-20260825"
    / "artifact-manifest.json"
)


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("fixture is not an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PrimaryExactPowerTests(unittest.TestCase):
    def test_exact_values_and_minimum_sample_sizes(self) -> None:
        self.assertEqual(
            power.exact_power(
                16,
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.PRIMARY_ALPHA,
            ),
            Fraction(14454764201349, 19531250000000),
        )
        self.assertEqual(
            power.exact_power(
                24,
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.PRIMARY_ALPHA,
            ),
            Fraction(3585708077179064276673, 3906250000000000000000),
        )
        self.assertEqual(
            power.minimum_world_count_for_power(
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.PRIMARY_ALPHA,
                power.TARGET_POWER,
                minimum_world_count=1,
                maximum_world_count=128,
            ),
            23,
        )
        self.assertEqual(
            power.minimum_world_count_for_power(
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.PRIMARY_ALPHA,
                power.TARGET_POWER,
                minimum_world_count=1,
                maximum_world_count=128,
                required_multiple=4,
            ),
            24,
        )


class PrimaryConfigPlanResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _read(CONFIG_PATH)
        cls.config_sha = _sha(CONFIG_PATH)
        cls.source_sha = "a" * 64

    def _build_plan(self) -> dict[str, object]:
        return power.build_power_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            artifact_manifest_path=MANIFEST_PATH,
            require_current_source=False,
        )

    def test_config_route_and_claim_drift_rejected(self) -> None:
        power.validate_config(self.config)
        for path in (
            ("route_family", "primary_route", "route_binding_sha256"),
            ("primary_hypothesis", "claim"),
        ):
            tampered = copy.deepcopy(self.config)
            target = tampered
            for key in path[:-1]:
                target = target[key]  # type: ignore[index]
            target[path[-1]] = (  # type: ignore[index]
                "0" * 64 if path[-1].endswith("sha256") else "overbroad claim"
            )
            with self.assertRaises(power.PrimaryPowerError):
                power.validate_config(tampered)
        tampered = copy.deepcopy(self.config)
        tampered["route_family"]["exploratory_routes"][0][  # type: ignore[index]
            "can_replace_primary"
        ] = True
        with self.assertRaises(power.PrimaryPowerError):
            power.validate_config(tampered)
        tampered = copy.deepcopy(self.config)
        tampered["upstream_geometry"][  # type: ignore[index]
            "artifact_manifest_relative_path"
        ] = "../../private.json"
        with self.assertRaises(power.PrimaryPowerError):
            power.validate_config(tampered)

    def test_plan_q4_fail_q6_pass_and_bindings(self) -> None:
        plan = self._build_plan()
        power.validate_power_plan(
            self.config,
            plan,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            artifact_manifest_path=MANIFEST_PATH,
            require_current_source=False,
        )
        self.assertEqual(
            [row["passes_target_power"] for row in plan["candidate_designs"]],
            [False, True],
        )
        self.assertEqual(plan["minimum_world_count_unbalanced"], 23)
        self.assertEqual(plan["minimum_world_count_four_stratum_balanced"], 24)
        self.assertEqual(plan["primary_route"]["route_id"], "deepseek-pro")  # type: ignore[index]
        self.assertTrue(  # type: ignore[index]
            plan["candidate_designs"][1]["later_sealed_matching_required"]
        )

    def test_plan_result_review_barrier_and_drift(self) -> None:
        plan = self._build_plan()
        with self.assertRaises(power.PrimaryPowerError):
            power.build_power_result(self.config, plan)
        tampered = copy.deepcopy(plan)
        tampered["primary_route"]["route_id"] = "deepseek-flash"  # type: ignore[index]
        with self.assertRaises(power.PrimaryPowerError):
            power.validate_power_plan(self.config, tampered, require_current_source=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            power._emit_json_exclusive_0600(plan, path)
            file_sha = _sha(path)
            result = power.build_power_result(
                self.config,
                plan,
                reviewed_plan_sha256=plan["plan_sha256"],
                reviewed_plan_file_sha256=file_sha,
                plan_path=path,
                artifact_manifest_path=MANIFEST_PATH,
                require_current_source=False,
            )
            power.validate_power_result(
                self.config,
                plan,
                result,
                reviewed_plan_sha256=plan["plan_sha256"],
                reviewed_plan_file_sha256=file_sha,
                plan_path=path,
                artifact_manifest_path=MANIFEST_PATH,
                require_current_source=False,
            )
            changed = copy.deepcopy(result)
            changed["classification"]["q6"] = "fail"  # type: ignore[index]
            with self.assertRaises(power.PrimaryPowerError):
                power.validate_power_result(
                    self.config,
                    plan,
                    changed,
                    reviewed_plan_sha256=plan["plan_sha256"],
                    reviewed_plan_file_sha256=file_sha,
                    plan_path=path,
                    artifact_manifest_path=MANIFEST_PATH,
                    require_current_source=False,
                )

    def test_safe_manifest_only_and_no_provider_imports(self) -> None:
        source = inspect.getsource(power)
        self.assertNotIn("OpenAICompatibleGenerator", source)
        self.assertNotIn("load_provider_credentials", source)
        plan = self._build_plan()
        self.assertEqual(
            plan["file_bindings"]["upstream_artifact_manifest"][  # type: ignore[index]
                "artifact_manifest_sha256"
            ],
            _read(MANIFEST_PATH)["manifest_sha256"],
        )
        with mock.patch.object(
            power,
            "_read_json_file",
            wraps=power._read_json_file,
        ) as reader:
            power._check_upstream_binding(self.config, artifact_manifest_path=MANIFEST_PATH)
            self.assertEqual(reader.call_count, 1)

    def test_exclusive_0600_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "primary.json"
            power._emit_json_exclusive_0600({"ok": True}, output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(power.PrimaryPowerError):
                power._emit_json_exclusive_0600({"ok": False}, output)


if __name__ == "__main__":
    unittest.main()
