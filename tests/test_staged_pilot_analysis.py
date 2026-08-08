from __future__ import annotations

import copy
import unittest

from src.pilot_analysis import analyze_pilot
from src.staged_pilot_analysis import (
    PRIVATE_TEST_RELEASE_RULE,
    StagedPilotAnalysisError,
    analyze_staged_snapshot,
)
from tests.test_pilot_analysis import _as_volcengine, _summary


def _stage_snapshot(world_count: int) -> dict:
    source = _as_volcengine(_summary())
    source["kind"] = "staged-development-pilot-snapshot"
    source["mode"] = "staged-development-pilot-offline-finalized"
    source["worlds"] = source["worlds"][:world_count]
    source["runs"] = [
        run for run in source["runs"] if run["world"]["index"] < world_count
    ]
    expected_runs = world_count * 7
    expected_calls = expected_runs * 20
    source["model"]["finish_reason_counts"] = {"stop": expected_calls}
    budget = source["budget"]
    budget["generation_calls_planned"] = expected_calls
    budget["generation_calls_completed"] = expected_calls
    budget["provider_requests"] = expected_calls
    budget["retry_count"] = 0
    budget["run_count"] = expected_runs
    budget["max_output_tokens_planned"] = expected_calls * 256
    budget["max_output_tokens_completed_ceiling"] = expected_calls * 256
    budget["actual_input_tokens"] = sum(
        run["budget"]["actual_input_tokens"] for run in source["runs"]
    )
    budget["actual_output_tokens"] = sum(
        run["budget"]["actual_output_tokens"] for run in source["runs"]
    )
    budget["actual_billed_tokens"] = (
        budget["actual_input_tokens"] + budget["actual_output_tokens"]
    )
    budget["latency_ms_total"] = sum(
        run["budget"]["latency_ms_total"] for run in source["runs"]
    )
    budget["prompt_cache_hit_tokens"] = None
    budget["prompt_cache_miss_tokens"] = None
    definitions = {
        2: ("S1", [0, 1]),
        4: ("S2", [2, 3]),
        8: ("S3", [4, 5, 6, 7]),
    }
    stage_id, new_worlds = definitions[world_count]
    source["stage"] = {
        "stage_id": stage_id,
        "cumulative_world_count": world_count,
        "included_world_indices": list(range(world_count)),
        "new_world_indices": new_worlds,
        "required_checkpoint_count": expected_runs,
        "required_world_seal_count": world_count,
        "final_classification_eligible": world_count == 8,
        "private_test_release_rule": PRIVATE_TEST_RELEASE_RULE,
    }
    source["campaign"] = {
        "manifest_sha256": "a" * 64,
        "config_sha256": source["config_hash"],
        "source_manifest_sha256": "b" * 64,
        "plan_sha256": "c" * 64,
    }
    source["execution_audit"] = {
        "committed_scientific_calls": expected_calls,
        "abandoned_operational_calls": 0,
        "discarded_operational_calls": 0,
        "ambiguous_operational_calls": 0,
        "legacy_234_attempt_imported": False,
    }
    return source


class StagedPilotAnalysisTests(unittest.TestCase):
    def test_two_world_snapshot_is_promising_but_never_final_positive(self) -> None:
        result = analyze_staged_snapshot(_stage_snapshot(2))

        self.assertEqual(result["classification"], "interim_descriptive_only")
        self.assertEqual(result["boundary_signal"], "promising_signal")
        self.assertFalse(result["final_classification_eligible"])
        self.assertEqual(result["core_hypothesis_status"], "not_decided")
        self.assertTrue(result["optional_stopping_present"])
        self.assertTrue(result["engineering"]["passed"])
        self.assertAlmostEqual(
            result["performance"]["E_minus_strongest_nonadaptive"], 0.10
        )
        self.assertEqual(len(result["nonoverlapping_batch_diagnostics"]), 1)

    def test_four_world_snapshot_adds_a_second_nonoverlapping_diagnostic(self) -> None:
        result = analyze_staged_snapshot(_stage_snapshot(4))

        self.assertEqual(result["stage"]["stage_id"], "S2")
        self.assertEqual(result["classification"], "interim_descriptive_only")
        self.assertEqual(len(result["nonoverlapping_batch_diagnostics"]), 2)
        self.assertTrue(
            result["stage"]["provisional_comparator_not_frozen"]
        )

    def test_eight_world_science_matches_the_existing_frozen_analyzer(self) -> None:
        snapshot = _stage_snapshot(8)
        staged = analyze_staged_snapshot(snapshot)
        original = analyze_pilot(_as_volcengine(_summary()))

        self.assertEqual(staged["classification"], original["classification"])
        self.assertEqual(
            staged["performance"]["mean_hidden_test_accuracy_by_arm"],
            original["performance"]["mean_hidden_test_accuracy_by_arm"],
        )
        self.assertEqual(
            staged["performance"]["strongest_nonadaptive_comparators"],
            original["performance"]["strongest_nonadaptive_comparators"],
        )
        self.assertAlmostEqual(
            staged["performance"]["E_minus_strongest_nonadaptive"],
            original["performance"]["E_minus_strongest_nonadaptive"],
        )
        self.assertEqual(
            staged["manipulation"]["H_minus_L_unique_canonical_per_call"],
            original["manipulation"]["H_minus_L_unique_canonical_per_call"],
        )
        self.assertTrue(staged["final_classification_eligible"])
        self.assertEqual(staged["pilot_completion_status"], "complete")

    def test_schema_or_manipulation_failure_is_only_an_interim_signal(self) -> None:
        schema = _stage_snapshot(2)
        for run in schema["runs"]:
            if run["arm_id"] == "E":
                for candidate in run["candidates"]:
                    candidate["candidate_format"] = "invalid_json"
        schema_result = analyze_staged_snapshot(schema)
        self.assertEqual(
            schema_result["boundary_signal"], "not_interpretable_schema"
        )
        self.assertEqual(
            schema_result["classification"], "interim_descriptive_only"
        )

        manipulation = _stage_snapshot(2)
        for run in manipulation["runs"]:
            if run["arm_id"] == "H":
                for candidate_index, candidate in enumerate(run["candidates"]):
                    candidate["canonical_hash"] = f"shared-c-{candidate_index // 2}"
                    candidate["behavior_hash"] = f"shared-b-{candidate_index // 2}"
        manipulation_result = analyze_staged_snapshot(manipulation)
        self.assertEqual(
            manipulation_result["boundary_signal"],
            "manipulation_not_yet_supported",
        )

    def test_campaign_or_legacy_import_tamper_fails_engineering_gate(self) -> None:
        source = _stage_snapshot(2)
        source["campaign"]["plan_sha256"] = "not-a-hash"
        source["execution_audit"]["legacy_234_attempt_imported"] = True
        result = analyze_staged_snapshot(source)

        self.assertFalse(result["engineering"]["passed"])
        self.assertEqual(
            result["boundary_signal"], "not_interpretable_engineering"
        )

    def test_public_candidate_expression_is_rejected_as_engineering_drift(self) -> None:
        source = _stage_snapshot(2)
        source["runs"][0]["candidates"][0]["candidate_expression"] = "(var x1)"
        result = analyze_staged_snapshot(source)

        self.assertFalse(result["engineering"]["passed"])
        self.assertIn(
            "public snapshot contains a sealed candidate expression",
            result["engineering"]["issues"],
        )

    def test_stage_declaration_must_use_exact_frozen_boundary(self) -> None:
        source = _stage_snapshot(2)
        source["stage"]["included_world_indices"] = [1, 0]
        result = analyze_staged_snapshot(source)
        self.assertFalse(result["engineering"]["passed"])

        malformed = copy.deepcopy(source)
        malformed["stage"]["cumulative_world_count"] = 3
        with self.assertRaises(StagedPilotAnalysisError):
            analyze_staged_snapshot(malformed)

    def test_recovery_forces_resource_sensitivity(self) -> None:
        source = _stage_snapshot(2)
        source["execution_audit"]["abandoned_operational_calls"] = 15
        source["execution_audit"]["discarded_operational_calls"] = 14
        source["execution_audit"]["ambiguous_operational_calls"] = 1
        result = analyze_staged_snapshot(source)

        self.assertTrue(result["resource_sensitivity"]["required"])
        self.assertFalse(
            result["resource_sensitivity"]["actual_token_matched_claim_allowed"]
        )
        self.assertEqual(
            result["resource_sensitivity"]["primary_estimand"],
            "first-complete-episode-under-frozen-recovery-policy",
        )


if __name__ == "__main__":
    unittest.main()
