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

from src import spark_strong_k4_utilization_power as power
from src.provenance import PROJECT_ROOT


CONFIG_PATH = PROJECT_ROOT / "configs" / "spark-strong-k4-utilization-power-v1.json"
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


class ExactPowerTests(unittest.TestCase):
    def test_binomial_upper_tail_hand_case(self) -> None:
        self.assertEqual(
            power.binomial_upper_tail(4, 3, Fraction(1, 2)),
            Fraction(5, 16),
        )
        self.assertEqual(
            power.critical_favorable_count(4, Fraction(1, 5)),
            4,
        )

    def test_exact_power_matches_frozen_decimal_contract(self) -> None:
        expected = {
            16: 0.5195276335337472,
            24: 0.7898078702451884,
            32: 0.9161773022953812,
        }
        for n, display in expected.items():
            exact = power.exact_power(
                n,
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.CONSERVATIVE_ALPHA,
            )
            self.assertAlmostEqual(float(exact), display, places=15)
            self.assertEqual(
                power.fraction_payload(exact)["numerator"], exact.numerator
            )

    def test_minimum_world_search_and_known_gate(self) -> None:
        for n in range(1, 31):
            self.assertLess(
                power.exact_power(
                    n,
                    power.FROZEN_P_FAVORABLE,
                    power.FROZEN_P_ADVERSE,
                    power.CONSERVATIVE_ALPHA,
                ),
                power.TARGET_POWER,
            )
        self.assertGreaterEqual(
            power.exact_power(
                31,
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.CONSERVATIVE_ALPHA,
            ),
            power.TARGET_POWER,
        )
        self.assertEqual(
            power.minimum_world_count_for_power(
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.CONSERVATIVE_ALPHA,
                power.TARGET_POWER,
                minimum_world_count=1,
                maximum_world_count=128,
            ),
            31,
        )
        self.assertEqual(
            power.minimum_world_count_for_power(
                power.FROZEN_P_FAVORABLE,
                power.FROZEN_P_ADVERSE,
                power.CONSERVATIVE_ALPHA,
                power.TARGET_POWER,
                minimum_world_count=1,
                maximum_world_count=128,
                required_multiple=4,
            ),
            32,
        )

    def test_world_classification_and_tie_conditional_sign_test(self) -> None:
        self.assertEqual(
            power.classify_world_scores(1, 0)["classification"], "favorable"
        )
        self.assertEqual(
            power.classify_world_scores(0, 1)["classification"], "adverse"
        )
        self.assertEqual(
            power.classify_world_scores(1, 1)["classification"], "tie"
        )
        summary = power.summarize_world_scores(
            [1, 1, 0, 2], [0, 0, 1, 2], alpha=Fraction(1, 20)
        )
        self.assertEqual(summary["tie_count"], 1)
        self.assertEqual(summary["non_tie_count"], 3)
        self.assertEqual(
            summary["primary_test"]["exact_one_sided_p_value"],
            power.fraction_payload(Fraction(1, 2)),
        )


class ConfigPlanResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _read(CONFIG_PATH)
        cls.config_sha = _sha(CONFIG_PATH)
        cls.source_sha = "a" * 64

    def test_config_schema_and_frozen_constants_reject_drift(self) -> None:
        power.validate_config(self.config)
        tampered = copy.deepcopy(self.config)
        tampered["route_family"]["family_alpha"] = {  # type: ignore[index]
            "numerator": 1,
            "denominator": 19,
        }
        with self.assertRaises(power.UtilizationPowerError):
            power.validate_config(tampered)
        extra = copy.deepcopy(self.config)
        extra["unexpected"] = True
        with self.assertRaises(power.UtilizationPowerError):
            power.validate_config(extra)
        claim_drift = copy.deepcopy(self.config)
        claim_drift["candidate_designs"][0][  # type: ignore[index]
            "claim_limit"
        ] = "overbroad claim"
        with self.assertRaises(power.UtilizationPowerError):
            power.validate_config(claim_drift)
    def test_plan_result_bindings_and_tiers(self) -> None:
        plan = power.build_power_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            require_current_source=False,
        )
        power.validate_power_plan(
            self.config,
            plan,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            require_current_source=False,
        )
        self.assertEqual(
            [row["passes_target_power"] for row in plan["candidate_designs"]],
            [False, False, True],
        )
        self.assertEqual(
            [row["classification"] for row in plan["tier_results"]],
            [
                "strict_unique_switch_power_inadequate_under_available_geometry",
                "degraded_two_choice_power_adequate_at_frozen_sesoi",
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            power._emit_json_exclusive_0600(plan, plan_path)
            plan_file_sha = _sha(plan_path)
            result = power.build_power_result(
                self.config,
                plan,
                reviewed_plan_sha256=plan["plan_sha256"],
                reviewed_plan_file_sha256=plan_file_sha,
                plan_path=plan_path,
                require_current_source=False,
            )
            power.validate_power_result(
                self.config,
                plan,
                result,
                reviewed_plan_sha256=plan["plan_sha256"],
                reviewed_plan_file_sha256=plan_file_sha,
                plan_path=plan_path,
                require_current_source=False,
            )
        self.assertFalse(result["final_benchmark_minted"])

    def test_plan_and_result_drift_and_review_barrier(self) -> None:
        plan = power.build_power_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            require_current_source=False,
        )
        with self.assertRaises(power.UtilizationPowerError):
            power.build_power_result(self.config, plan)
        tampered_plan = copy.deepcopy(plan)
        tampered_plan["candidate_designs"][0]["world_count"] = 17  # type: ignore[index]
        with self.assertRaises(power.UtilizationPowerError):
            power.validate_power_plan(
                self.config,
                tampered_plan,
                require_current_source=False,
            )
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            power._emit_json_exclusive_0600(plan, plan_path)
            plan_file_sha = _sha(plan_path)
            with self.assertRaises(power.UtilizationPowerError):
                power.build_power_result(
                    self.config,
                    plan,
                    reviewed_plan_sha256="0" * 64,
                    reviewed_plan_file_sha256=plan_file_sha,
                    plan_path=plan_path,
                    require_current_source=False,
                )
            result = power.build_power_result(
                self.config,
                plan,
                reviewed_plan_sha256=plan["plan_sha256"],
                reviewed_plan_file_sha256=plan_file_sha,
                plan_path=plan_path,
                require_current_source=False,
            )
            tampered_result = copy.deepcopy(result)
            tampered_result["classification"][  # type: ignore[index]
                "degraded"
            ] = "strict_designs_pass"
            with self.assertRaises(power.UtilizationPowerError):
                power.validate_power_result(
                    self.config,
                    plan,
                    tampered_result,
                    reviewed_plan_sha256=plan["plan_sha256"],
                    reviewed_plan_file_sha256=plan_file_sha,
                    plan_path=plan_path,
                    require_current_source=False,
                )

    def test_source_binding_optional_and_required(self) -> None:
        plan = power.build_power_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            require_current_source=False,
        )
        power.validate_power_plan(
            self.config,
            plan,
            source_manifest_sha256=self.source_sha,
            require_current_source=False,
        )
        with mock.patch.object(
            power,
            "source_manifest",
            return_value={"source_manifest_sha256": "b" * 64},
        ):
            with self.assertRaises(power.UtilizationPowerError):
                power.validate_power_plan(
                    self.config,
                    plan,
                    require_current_source=True,
                )

    def test_config_and_reviewed_plan_raw_bytes_are_bound(self) -> None:
        with self.assertRaises(power.UtilizationPowerError):
            power.build_power_plan(
                self.config,
                config_file_sha256="0" * 64,
                source_manifest_sha256=self.source_sha,
                require_current_source=False,
            )
        plan = power.build_power_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            require_current_source=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            power._emit_json_exclusive_0600(plan, plan_path)
            reviewed_file_sha = _sha(plan_path)
            plan_path.write_text(
                json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(power.UtilizationPowerError):
                power.build_power_result(
                    self.config,
                    plan,
                    reviewed_plan_sha256=plan["plan_sha256"],
                    reviewed_plan_file_sha256=reviewed_file_sha,
                    plan_path=plan_path,
                    require_current_source=False,
                )

    def test_bounded_json_reader_rejects_large_input_before_parse(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            oversized = Path(directory) / "plan.json"
            with oversized.open("wb") as handle:
                handle.truncate(1025)
            with self.assertRaisesRegex(
                power.UtilizationPowerError,
                "pre-read size limit",
            ):
                power._read_json_bounded(
                    oversized,
                    "power plan",
                    maximum_bytes=1024,
                    expected_name="plan.json",
                )

    def test_safe_manifest_binding_does_not_touch_private_result(self) -> None:
        plan = power.build_power_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256=self.source_sha,
            artifact_manifest_path=MANIFEST_PATH,
            require_current_source=False,
        )
        self.assertEqual(
            plan["file_bindings"]["upstream_artifact_manifest"][  # type: ignore[index]
                "artifact_manifest_sha256"
            ],
            _read(MANIFEST_PATH)["manifest_sha256"],
        )
        source = inspect.getsource(power)
        self.assertNotIn("OpenAICompatibleGenerator", source)
        self.assertNotIn("load_provider_credentials", source)

    def test_exclusive_0600_writer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "power.json"
            power._emit_json_exclusive_0600({"ok": True}, output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(power.UtilizationPowerError):
                power._emit_json_exclusive_0600({"ok": False}, output)


if __name__ == "__main__":
    unittest.main()
