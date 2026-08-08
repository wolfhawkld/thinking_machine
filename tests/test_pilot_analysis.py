from __future__ import annotations

import copy
import hashlib
import unittest

from src.pilot_analysis import (
    PILOT_ARMS,
    PILOT_MAX_OUTPUT_TOKENS,
    PILOT_MODEL,
    PILOT_OFFICIAL_PROVIDER,
    PILOT_VOLCENGINE_ENDPOINT,
    PILOT_VOLCENGINE_PROVIDER,
    PILOT_VOLCENGINE_RESPONSE_MODEL,
    PILOT_WORLDS,
    _expected_config_hash,
    analyze_pilot,
)


def _provider_contract(provider: str) -> dict:
    if provider == PILOT_VOLCENGINE_PROVIDER:
        return {
            "profile": provider,
            "request_model": PILOT_MODEL,
            "expected_response_model": PILOT_VOLCENGINE_RESPONSE_MODEL,
            "capability_contract": {
                "prompt_cache_usage": {
                    "observed_capability": (
                        "hit_and_miss_token_counts_unavailable_after_adapter_normalization"
                    ),
                    "pass_requirement": (
                        "both_fields_null_or_omitted_after_adapter_normalization_for_every_call"
                    ),
                },
                "system_fingerprint": {
                    "observed_capability": (
                        "system_fingerprint_unavailable_after_adapter_normalization"
                    ),
                    "pass_requirement": (
                        "null_or_omitted_after_adapter_normalization_for_every_call"
                    ),
                },
            },
            "endpoint_contract": {
                "binding": "fixed_exact_after_removing_trailing_slashes",
                "normalization": "remove_trailing_slashes_only",
                "normalized_url_sha256": hashlib.sha256(
                    PILOT_VOLCENGINE_ENDPOINT.encode()
                ).hexdigest(),
                "contract_satisfied": True,
            },
            "contract_satisfied": True,
        }
    return {
        "profile": provider,
        "request_model": PILOT_MODEL,
        "expected_response_model": PILOT_MODEL,
        "capability_contract": {
            "prompt_cache_usage": {
                "observed_capability": "hit_and_miss_token_counts_reported",
                "pass_requirement": "complete_accounting_for_every_call",
            },
            "system_fingerprint": {
                "observed_capability": "system_fingerprint_reported",
                "pass_requirement": "nonempty_and_stable_for_every_call",
            },
        },
        "endpoint_contract": {
            "binding": "runtime_credential_sha256_audit_only",
            "normalization": "remove_trailing_slashes_only",
            "normalized_url_sha256": hashlib.sha256(
                b"https://api.deepseek.example/v1"
            ).hexdigest(),
            "contract_satisfied": True,
        },
        "contract_satisfied": True,
    }


def _candidate(arm: str, world_index: int, index: int) -> dict:
    if arm == "L":
        unique_index = index // 2
    else:
        unique_index = index
    return {
        "round_index": index // 4,
        "candidate_index": index % 4,
        "syntax_valid": True,
        "runtime_valid": True,
        "canonical_hash": f"{arm}-{world_index}-c-{unique_index}",
        "behavior_hash": f"{arm}-{world_index}-b-{unique_index}",
        "candidate_format": "json_expression",
        "input_tokens": 100,
        "output_tokens": 10,
        "latency_ms": 10.0,
        "provider_request_count": 1,
        "seed_supported": False,
        "provider_model": "deepseek-v4-flash",
        "finish_reason": "stop",
        "prompt_cache_hit_tokens": 25,
        "prompt_cache_miss_tokens": 75,
        "reasoning_tokens": 0,
        "provider_fingerprint": "fp-pilot-test",
    }


def _summary() -> dict:
    scores = {
        "L": 0.40,
        "M": 0.60,
        "H": 0.50,
        "A": 0.55,
        "C": 0.58,
        "MTX": 0.59,
        "E": 0.70,
    }
    worlds = [
        {"index": index, "seed": seed, "depth": depth, "world_hash": f"w-{seed}"}
        for index, (seed, depth) in enumerate(PILOT_WORLDS)
    ]
    runs = []
    for world_index, (seed, depth) in enumerate(PILOT_WORLDS):
        for arm in PILOT_ARMS:
            candidates = [_candidate(arm, world_index, index) for index in range(20)]
            runs.append(
                {
                    "arm_id": arm,
                    "world": {"index": world_index, "seed": seed, "depth": depth},
                    "budget": {
                        "generation_calls_planned": 20,
                        "generation_calls_completed": 20,
                        "max_output_tokens_per_call": PILOT_MAX_OUTPUT_TOKENS,
                        "max_output_tokens_planned": 20 * PILOT_MAX_OUTPUT_TOKENS,
                        "max_output_tokens_completed_ceiling": (
                            20 * PILOT_MAX_OUTPUT_TOKENS
                        ),
                        "actual_usage_available": True,
                        "actual_input_tokens": 2_000,
                        "actual_output_tokens": 200,
                        "actual_billed_tokens": 2_200,
                        "provider_requests": 20,
                        "retry_count": 0,
                        "latency_ms_total": 200.0,
                        "latency_ms_mean": 10.0,
                        "prompt_cache_hit_tokens": 500,
                        "prompt_cache_miss_tokens": 1_500,
                        "reasoning_tokens": 0,
                        "final_test_points_planned": 1,
                        "final_test_points_evaluated": 1,
                    },
                    "candidates": candidates,
                    "final_test": {"evaluated": True, "accuracy": scores[arm]},
                }
            )
    return {
        "schema_version": 1,
        "experiment": "adaptive-entropy-scheduling",
        "config_status": "development-only",
        "config_hash": _expected_config_hash(PILOT_OFFICIAL_PROVIDER),
        "mode": "development-pilot-live",
        "evidence": False,
        "evidence_scope": "non-evidence",
        "provenance": {"source_manifest_sha256": "p" * 64},
        "provider_contract": _provider_contract(PILOT_OFFICIAL_PROVIDER),
        "execution_contract": {
            "logical_calls": 1_120,
            "runs": 56,
            "max_output_tokens_per_call": PILOT_MAX_OUTPUT_TOKENS,
            "provider_retries": 0,
            "resume_supported": False,
            "private_test_evaluation": (
                "globally_delayed_until_all_generation_calls_completed"
            ),
        },
        "model": {
            "configured": {
                "provider": PILOT_OFFICIAL_PROVIDER,
                "name": PILOT_MODEL,
                "snapshot": None,
                "structured_output": True,
            },
            "observed_response_models": [PILOT_MODEL],
            "observed_system_fingerprints": ["fp-pilot-test"],
            "finish_reason_counts": {"stop": 1_120},
        },
        "worlds": worlds,
        "runs": runs,
        "budget": {
            "generation_calls_planned": 1120,
            "generation_calls_completed": 1120,
            "provider_requests": 1120,
            "retry_count": 0,
            "run_count": 56,
            "max_output_tokens_planned": 286_720,
            "max_output_tokens_completed_ceiling": 286_720,
            "actual_usage_available": True,
            "actual_input_tokens": 112_000,
            "actual_output_tokens": 11_200,
            "actual_billed_tokens": 123_200,
            "latency_ms_total": 11_200.0,
            "prompt_cache_hit_tokens": 28_000,
            "prompt_cache_miss_tokens": 84_000,
            "reasoning_tokens": 0,
        },
    }


def _recalculate_usage(summary: dict) -> None:
    total_input = 0
    total_output = 0
    total_latency = 0.0
    total_hit = 0
    total_miss = 0
    cache_available = True
    for run in summary["runs"]:
        candidates = run["candidates"]
        input_tokens = sum(candidate["input_tokens"] for candidate in candidates)
        output_tokens = sum(candidate["output_tokens"] for candidate in candidates)
        latency = sum(candidate["latency_ms"] for candidate in candidates)
        budget = run["budget"]
        budget["actual_input_tokens"] = input_tokens
        budget["actual_output_tokens"] = output_tokens
        budget["actual_billed_tokens"] = input_tokens + output_tokens
        budget["latency_ms_total"] = latency
        budget["latency_ms_mean"] = latency / len(candidates)
        hits = [candidate["prompt_cache_hit_tokens"] for candidate in candidates]
        misses = [candidate["prompt_cache_miss_tokens"] for candidate in candidates]
        if all(value is not None for value in hits + misses):
            budget["prompt_cache_hit_tokens"] = sum(hits)
            budget["prompt_cache_miss_tokens"] = sum(misses)
            total_hit += sum(hits)
            total_miss += sum(misses)
        else:
            budget["prompt_cache_hit_tokens"] = None
            budget["prompt_cache_miss_tokens"] = None
            cache_available = False
        total_input += input_tokens
        total_output += output_tokens
        total_latency += latency
    top = summary["budget"]
    top["actual_input_tokens"] = total_input
    top["actual_output_tokens"] = total_output
    top["actual_billed_tokens"] = total_input + total_output
    top["latency_ms_total"] = total_latency
    top["prompt_cache_hit_tokens"] = total_hit if cache_available else None
    top["prompt_cache_miss_tokens"] = total_miss if cache_available else None


def _as_volcengine(summary: dict) -> dict:
    result = copy.deepcopy(summary)
    result["config_hash"] = _expected_config_hash(PILOT_VOLCENGINE_PROVIDER)
    result["provider_contract"] = _provider_contract(PILOT_VOLCENGINE_PROVIDER)
    result["model"]["configured"]["provider"] = PILOT_VOLCENGINE_PROVIDER
    result["model"]["observed_response_models"] = [
        PILOT_VOLCENGINE_RESPONSE_MODEL
    ]
    result["model"]["observed_system_fingerprints"] = []
    for run in result["runs"]:
        for candidate in run["candidates"]:
            candidate["provider_model"] = PILOT_VOLCENGINE_RESPONSE_MODEL
            candidate["prompt_cache_hit_tokens"] = None
            candidate["prompt_cache_miss_tokens"] = None
            candidate["provider_fingerprint"] = None
    _recalculate_usage(result)
    return result


class PilotAnalysisTests(unittest.TestCase):
    def test_clean_margin_is_preliminary_positive(self) -> None:
        result = analyze_pilot(_summary())

        self.assertEqual(result["classification"], "preliminary_positive")
        self.assertTrue(result["engineering"]["passed"])
        self.assertTrue(result["candidate_schema"]["passed"])
        self.assertTrue(result["manipulation"]["passed"])
        self.assertEqual(
            result["performance"]["strongest_nonadaptive_comparators"], ["M"]
        )
        self.assertAlmostEqual(
            result["performance"]["E_minus_strongest_nonadaptive"], 0.10
        )
        self.assertFalse(result["resource_sensitivity"]["required"])
        self.assertTrue(result["stop_after_this_analysis"])

    def test_nonpositive_delta_is_current_implementation_negative(self) -> None:
        summary = _summary()
        for run in summary["runs"]:
            if run["arm_id"] == "E":
                run["final_test"]["accuracy"] = 0.50

        result = analyze_pilot(summary)

        self.assertEqual(
            result["classification"], "current_operationalization_negative"
        )

    def test_submargin_gain_is_indeterminate(self) -> None:
        summary = _summary()
        for run in summary["runs"]:
            if run["arm_id"] == "E":
                run["final_test"]["accuracy"] = 0.625

        result = analyze_pilot(summary)

        self.assertEqual(result["classification"], "indeterminate")
        self.assertIn("less than", result["classification_reasons"][0])

    def test_schema_or_manipulation_failure_is_indeterminate(self) -> None:
        schema_failure = _summary()
        for run in schema_failure["runs"]:
            if run["arm_id"] == "E":
                for candidate in run["candidates"][:5]:
                    candidate["candidate_format"] = "invalid_json"
        result = analyze_pilot(schema_failure)
        self.assertEqual(result["classification"], "indeterminate")
        self.assertFalse(result["candidate_schema"]["passed"])

        manipulation_failure = _summary()
        for run in manipulation_failure["runs"]:
            if run["arm_id"] == "H":
                for candidate in run["candidates"]:
                    candidate["canonical_hash"] = "one"
                    candidate["behavior_hash"] = "one"
        result = analyze_pilot(manipulation_failure)
        self.assertEqual(result["classification"], "indeterminate")
        self.assertFalse(result["manipulation"]["passed"])

    def test_engineering_and_token_drift_are_reported_separately(self) -> None:
        summary = _summary()
        first = summary["runs"][0]["candidates"][0]
        first["provider_fingerprint"] = "fp-drift"
        for run in summary["runs"]:
            if run["arm_id"] == "E":
                for candidate in run["candidates"]:
                    candidate["input_tokens"] = 130
                    candidate["prompt_cache_miss_tokens"] = 105

        result = analyze_pilot(summary)

        self.assertEqual(result["classification"], "indeterminate")
        self.assertFalse(result["engineering"]["passed"])
        self.assertTrue(result["resource_sensitivity"]["required"])

    def test_engineering_uses_the_amended_256_token_cap(self) -> None:
        at_cap = _summary()
        at_cap["runs"][0]["candidates"][0]["output_tokens"] = (
            PILOT_MAX_OUTPUT_TOKENS
        )
        _recalculate_usage(at_cap)
        self.assertTrue(analyze_pilot(at_cap)["engineering"]["passed"])

        over_cap = _summary()
        over_cap["runs"][0]["candidates"][0]["output_tokens"] = (
            PILOT_MAX_OUTPUT_TOKENS + 1
        )
        result = analyze_pilot(over_cap)
        self.assertFalse(result["engineering"]["passed"])
        self.assertEqual(result["classification"], "indeterminate")

    def test_same_scientific_data_classifies_identically_for_volcengine(self) -> None:
        official = analyze_pilot(_summary())
        volcengine = analyze_pilot(_as_volcengine(_summary()))

        self.assertEqual(
            volcengine["classification"], official["classification"]
        )
        self.assertEqual(
            volcengine["performance"], official["performance"]
        )
        self.assertEqual(
            volcengine["resource_sensitivity"],
            official["resource_sensitivity"],
        )
        self.assertTrue(volcengine["engineering"]["passed"])
        self.assertEqual(
            volcengine["engineering"]["fingerprint_status"],
            "capability_missing",
        )
        self.assertEqual(
            volcengine["engineering"]["stable_system_fingerprint_count"], 0
        )
        self.assertIn(
            "cannot be independently bound",
            volcengine["engineering"]["provenance_caveat"],
        )

    def test_volcengine_unexpected_telemetry_alias_or_contract_tamper_fails_gate(self) -> None:
        cases: dict[str, callable] = {
            "candidate_alias": lambda value: value["runs"][0]["candidates"][0].__setitem__(
                "provider_model", PILOT_MODEL
            ),
            "cache_partial": lambda value: value["runs"][0]["candidates"][0].__setitem__(
                "prompt_cache_hit_tokens", 0
            ),
            "fingerprint": lambda value: value["runs"][0]["candidates"][0].__setitem__(
                "provider_fingerprint", "unexpected"
            ),
            "observed_alias": lambda value: value["model"].__setitem__(
                "observed_response_models", [PILOT_MODEL]
            ),
            "observed_fingerprint": lambda value: value["model"].__setitem__(
                "observed_system_fingerprints", ["unexpected"]
            ),
            "run_cache": lambda value: value["runs"][0]["budget"].__setitem__(
                "prompt_cache_miss_tokens", 0
            ),
            "top_cache": lambda value: value["budget"].__setitem__(
                "prompt_cache_hit_tokens", 0
            ),
            "contract": lambda value: value["provider_contract"].__setitem__(
                "expected_response_model", PILOT_MODEL
            ),
            "endpoint_hash": lambda value: value["provider_contract"][
                "endpoint_contract"
            ].__setitem__("normalized_url_sha256", "0" * 64),
            "contract_satisfied": lambda value: value["provider_contract"].__setitem__(
                "contract_satisfied", False
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                summary = _as_volcengine(_summary())
                mutate(summary)
                result = analyze_pilot(summary)
                self.assertFalse(result["engineering"]["passed"])
                self.assertEqual(result["classification"], "indeterminate")

    def test_unknown_provider_and_provider_or_config_hash_drift_are_indeterminate(self) -> None:
        unknown = _summary()
        unknown["model"]["configured"]["provider"] = "unknown-provider"
        result = analyze_pilot(unknown)
        self.assertFalse(result["engineering"]["passed"])
        self.assertEqual(result["classification"], "indeterminate")

        provider_drift = _summary()
        provider_drift["model"]["configured"]["provider"] = (
            PILOT_VOLCENGINE_PROVIDER
        )
        result = analyze_pilot(provider_drift)
        self.assertFalse(result["engineering"]["passed"])

        hash_drift = _as_volcengine(_summary())
        hash_drift["config_hash"] = "0" * 64
        result = analyze_pilot(hash_drift)
        self.assertFalse(result["engineering"]["passed"])

    def test_official_missing_telemetry_and_execution_or_budget_drift_fail(self) -> None:
        missing_cache = _summary()
        missing_cache["runs"][0]["candidates"][0][
            "prompt_cache_hit_tokens"
        ] = None
        with self.assertRaises(ValueError):
            analyze_pilot(missing_cache)

        missing_fingerprint = _summary()
        missing_fingerprint["runs"][0]["candidates"][0][
            "provider_fingerprint"
        ] = None
        result = analyze_pilot(missing_fingerprint)
        self.assertFalse(result["engineering"]["passed"])

        for field in ("resume_supported", "private_test_evaluation"):
            with self.subTest(field=field):
                drift = _summary()
                drift["execution_contract"][field] = True
                result = analyze_pilot(drift)
                self.assertFalse(result["engineering"]["passed"])

        budget_drift = _summary()
        budget_drift["budget"]["max_output_tokens_planned"] -= 1
        result = analyze_pilot(budget_drift)
        self.assertFalse(result["engineering"]["passed"])


if __name__ == "__main__":
    unittest.main()
