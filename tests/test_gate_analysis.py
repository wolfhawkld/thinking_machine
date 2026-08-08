from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from src.gate_analysis import GateAnalysisError, analyze_gate, main


def _candidate(
    canonical_hash: str,
    behavior_hash: str,
    *,
    valid: bool = True,
    optional_usage: dict[str, int] | None = None,
) -> dict:
    return {
        "round_index": 0,
        "syntax_valid": valid,
        "runtime_valid": valid,
        "canonical_hash": canonical_hash,
        "behavior_hash": behavior_hash,
        "candidate_format": "json_expression",
        **(optional_usage or {}),
    }


def _run(
    arm_id: str,
    candidates: list[dict],
    *,
    probe: float,
    test: float,
    solved: bool = False,
) -> dict:
    return {
        "arm_id": arm_id,
        "candidates": candidates,
        "probe": {"final_selected_accuracy": probe},
        "final_test": {
            "evaluated": True,
            "accuracy": test,
            "world_solved": solved,
        },
        "budget": {
            "generation_calls_planned": 4,
            "generation_calls_completed": 4,
            "actual_usage_available": True,
            "actual_input_tokens": 400,
            "actual_output_tokens": 40,
            "actual_billed_tokens": 440,
            "provider_requests": 4,
            "retry_count": 0,
            "latency_ms_total": 40.0,
        },
    }


def synthetic_summary() -> dict:
    optional_e = [
        {
            "prompt_cache_hit_tokens": index,
            "prompt_cache_miss_tokens": 10,
            "reasoning_tokens": 0,
        }
        for index in range(1, 5)
    ]
    runs = [
        _run(
            "L",
            [
                _candidate("l1", "lb1"),
                _candidate("l1", "lb1"),
                _candidate("l2", "lb2"),
                _candidate("", "", valid=False),
            ],
            probe=0.50,
            test=0.25,
        ),
        _run(
            "H",
            [
                _candidate("h1", "hb1"),
                _candidate("h2", "hb2"),
                _candidate("h3", "hb3"),
                _candidate("h4", "hb3"),
            ],
            probe=0.60,
            test=0.50,
        ),
        _run(
            "M",
            [_candidate(f"m{i}", f"mb{i}") for i in range(4)],
            probe=0.70,
            test=0.50,
        ),
        _run(
            "MTX",
            [_candidate(f"x{i}", f"xb{i}") for i in range(4)],
            probe=0.75,
            test=0.50,
        ),
        _run(
            "E",
            [
                _candidate(f"e{i}", f"eb{i}", optional_usage=optional_e[i])
                for i in range(4)
            ],
            probe=0.90,
            test=1.0,
            solved=True,
        ),
    ]
    return {
        "schema_version": 1,
        "experiment": "synthetic-live-gate",
        "config_hash": "c" * 64,
        "evidence": False,
        "evidence_scope": "non-evidence",
        "mode": "injected-generator",
        "worlds": [{"world_hash": "w" * 64, "seed": 1, "depth": 3}],
        "runs": runs,
        "budget": {
            "run_count": 5,
            "generation_calls_planned": 20,
            "generation_calls_completed": 20,
            "actual_usage_available": True,
            "actual_input_tokens": 2000,
            "actual_output_tokens": 200,
            "actual_billed_tokens": 2200,
            "provider_requests": 20,
            "retry_count": 0,
            "latency_ms_total": 200.0,
            "token_fairness": {
                "available": True,
                "passed": True,
                "threshold": 0.02,
                "relative_range": 0.0,
                "mean_billed_tokens_per_call_by_arm": {
                    arm: 110.0 for arm in ("L", "H", "M", "MTX", "E")
                },
            },
        },
    }


class GateAnalysisTests(unittest.TestCase):
    def test_compact_metrics_contrasts_and_optional_usage_are_json_safe(self) -> None:
        result = analyze_gate(synthetic_summary())

        low = result["arms"]["L"]
        self.assertEqual(low["candidate_count"], 4)
        self.assertEqual(low["valid_count"], 3)
        self.assertEqual(low["valid_rate"], 0.75)
        self.assertEqual(low["unique_canonical_count"], 2)
        self.assertAlmostEqual(low["unique_canonical_rate"], 2 / 3)
        self.assertEqual(low["unique_canonical_per_planned_call"], 0.5)
        self.assertEqual(low["unique_behavior_count"], 2)
        self.assertAlmostEqual(low["unique_behavior_rate"], 2 / 3)
        self.assertEqual(low["billed_tokens_per_call"], 110.0)
        self.assertEqual(low["latency_ms_per_call"], 10.0)

        manipulation = result["contrasts"]["H_vs_L"]
        self.assertEqual(manipulation["unique_canonical_count_delta"], 2)
        self.assertAlmostEqual(manipulation["unique_canonical_rate_delta"], 1 / 3)
        self.assertAlmostEqual(manipulation["unique_behavior_rate_delta"], 1 / 12)
        self.assertEqual(
            manipulation["unique_canonical_per_planned_call_delta"], 0.5
        )
        self.assertEqual(
            manipulation["unique_behavior_per_planned_call_delta"], 0.25
        )
        self.assertEqual(
            result["contrasts"]["E_vs_M"]["final_test_accuracy_delta"],
            0.5,
        )
        self.assertEqual(result["contrasts"]["E_vs_MTX"]["world_solved_delta"], 1)

        optional = result["arms"]["E"]["optional_candidate_usage"]
        self.assertEqual(optional["prompt_cache_hit_tokens"]["reported_calls"], 4)
        self.assertEqual(optional["prompt_cache_hit_tokens"]["total"], 10)
        self.assertEqual(optional["reasoning_tokens"]["tokens_per_reported_call"], 0.0)
        absent = result["arms"]["M"]["optional_candidate_usage"]
        self.assertEqual(absent["reasoning_tokens"]["reported_calls"], 0)
        self.assertIsNone(absent["reasoning_tokens"]["total"])
        global_optional = result["global"]["optional_candidate_usage"]
        self.assertEqual(global_optional["prompt_cache_hit_tokens"]["reported_calls"], 4)
        self.assertFalse(global_optional["prompt_cache_hit_tokens"]["complete"])
        self.assertEqual(result["global"]["schema_adherence_rate"], 1.0)
        self.assertTrue(result["global"]["schema_adherence_passed"])
        self.assertEqual(
            result["global"]["candidate_format_by_round"]["0"][
                "schema_adherent_count"
            ],
            20,
        )
        self.assertEqual(
            result["arms"]["L"]["candidate_format_by_round"]["0"][
                "schema_adherent_count"
            ],
            4,
        )
        self.assertFalse(result["global"]["resource_sensitivity_required"])

        self.assertEqual(result["classification"], "promising")
        self.assertTrue(result["development_pilot_readiness"]["ready"])
        self.assertEqual(result["classification_scope"], "operational-only")
        self.assertIn("one serially executed world", result["caveat"])
        self.assertIn("statistical significance", result["caveat"])
        self.assertIn("cache order", result["caveat"])
        json.dumps(result, allow_nan=False, sort_keys=True)

    def test_clean_mixed_performance_is_neutral(self) -> None:
        summary = synthetic_summary()
        adaptive = next(run for run in summary["runs"] if run["arm_id"] == "E")
        adaptive["final_test"]["accuracy"] = 0.5
        adaptive["final_test"]["world_solved"] = False

        result = analyze_gate(summary)

        self.assertEqual(result["classification"], "neutral")
        self.assertIn("did not strictly exceed", result["classification_reasons"][0])

    def test_failed_fairness_requires_sensitivity_and_manipulation_is_concerning(self) -> None:
        summary = synthetic_summary()
        summary["budget"]["token_fairness"]["passed"] = False
        high = next(run for run in summary["runs"] if run["arm_id"] == "H")
        for candidate in high["candidates"]:
            candidate["behavior_hash"] = "one-behavior"

        result = analyze_gate(summary)

        self.assertEqual(result["classification"], "concerning")
        reasons = " ".join(result["classification_reasons"])
        self.assertNotIn("fairness failed", reasons)
        self.assertIn("behavioral uniqueness per planned call", reasons)
        self.assertTrue(result["global"]["resource_sensitivity_required"])
        self.assertTrue(result["development_pilot_readiness"]["ready"])
        self.assertNotIn("significant", reasons)

    def test_low_schema_adherence_is_concerning_and_legacy_is_derived(self) -> None:
        summary = synthetic_summary()
        low = next(run for run in summary["runs"] if run["arm_id"] == "L")
        low["candidates"][0]["candidate_format"] = "invalid_json"

        result = analyze_gate(summary)

        self.assertEqual(result["classification"], "concerning")
        self.assertIn("candidate-schema", " ".join(result["classification_reasons"]))
        self.assertEqual(result["arms"]["L"]["schema_adherence_rate"], 0.75)
        self.assertFalse(result["development_pilot_readiness"]["ready"])

        legacy = synthetic_summary()
        for run in legacy["runs"]:
            for candidate in run["candidates"]:
                candidate.pop("candidate_format")
                candidate["candidate_expression"] = "(var x1)"
        legacy["runs"][0]["candidates"][0]["candidate_expression"] = (
            "__INVALID_JSON_CANDIDATE_SCHEMA__"
        )
        legacy_result = analyze_gate(legacy)
        self.assertTrue(legacy_result["global"]["candidate_format_derived"])
        self.assertEqual(
            legacy_result["global"]["candidate_format_source"], "legacy-derived"
        )

    def test_nonzero_reasoning_tokens_are_concerning(self) -> None:
        summary = synthetic_summary()
        adaptive = next(run for run in summary["runs"] if run["arm_id"] == "E")
        adaptive["candidates"][0]["reasoning_tokens"] = 1

        result = analyze_gate(summary)

        self.assertEqual(result["classification"], "concerning")
        self.assertIn("reasoning tokens", " ".join(result["classification_reasons"]))

    def test_requires_exactly_one_world_and_required_contrast_arms(self) -> None:
        two_worlds = synthetic_summary()
        two_worlds["worlds"].append(copy.deepcopy(two_worlds["worlds"][0]))
        with self.assertRaisesRegex(GateAnalysisError, "exactly one world"):
            analyze_gate(two_worlds)

        missing_arm = synthetic_summary()
        missing_arm["runs"] = [run for run in missing_arm["runs"] if run["arm_id"] != "MTX"]
        with self.assertRaisesRegex(GateAnalysisError, "MTX"):
            analyze_gate(missing_arm)

    def test_cli_reads_json_and_writes_exclusively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "summary.json"
            output_path = root / "nested" / "analysis.json"
            input_path.write_text(json.dumps(synthetic_summary()), encoding="utf-8")

            with redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(
                    main(["--input", str(input_path), "--output", str(output_path)]),
                    0,
                )
            written = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(written["classification"], "promising")
            self.assertEqual(json.loads(stdout.getvalue())["kind"], written["kind"])

            with self.assertRaises(FileExistsError):
                main(["--input", str(input_path), "--output", str(output_path)])


if __name__ == "__main__":
    unittest.main()
