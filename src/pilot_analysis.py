"""Frozen descriptive decision rule for the eight-world development pilot.

This module deliberately makes no significance claim.  It converts one fully
completed development artifact into the predeclared stop-point classification:
preliminary positive, current-operationalization negative, or indeterminate.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .runner import CANDIDATE_FORMATS, DEFAULT_MAX_OUTPUT_TOKENS


PILOT_ANALYSIS_SCHEMA_VERSION = 1
PILOT_ARMS = ("L", "M", "H", "A", "C", "MTX", "E")
# L/H are reserved for the temperature-diversity manipulation.  The frozen
# performance comparator set contains the central fixed, two open-loop, and
# simultaneous multi-temperature baselines.
PILOT_COMPARATORS = ("M", "A", "C", "MTX")
PILOT_WORLDS = (
    (1001, 4),
    (1002, 5),
    (1003, 3),
    (1004, 4),
    (1005, 5),
    (1006, 3),
    (1007, 4),
    (1008, 5),
)
PILOT_MODEL = "deepseek-v4-flash"
PILOT_OFFICIAL_PROVIDER = "deepseek-openai-compatible"
PILOT_VOLCENGINE_PROVIDER = "volcengine-agent-plan-openai-compatible"
PILOT_VOLCENGINE_RESPONSE_MODEL = "deepseek-v4-flash-ga-260731"
PILOT_VOLCENGINE_ENDPOINT = "https://ark.cn-beijing.volces.com/api/plan/v3"
PILOT_MAX_OUTPUT_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS
CALLS_PER_RUN = 20
EXPECTED_RUNS = len(PILOT_WORLDS) * len(PILOT_ARMS)
EXPECTED_CALLS = EXPECTED_RUNS * CALLS_PER_RUN
OVERALL_SCHEMA_THRESHOLD = 0.90
PER_ARM_SCHEMA_THRESHOLD = 0.80
PRELIMINARY_POSITIVE_MARGIN = 0.05
TOKEN_EQUIVALENCE_THRESHOLD = 0.02
_PILOT_EPISODE = {
    "rounds": 5,
    "candidates_per_round": 4,
    "max_output_tokens": PILOT_MAX_OUTPUT_TOKENS,
    "archive_size": 4,
    "max_counterexamples_per_round": 2,
}
_PILOT_ARM_CONFIGS = {
    "L": {"kind": "fixed", "temperature": 0.2},
    "M": {"kind": "fixed", "temperature": 0.7},
    "H": {"kind": "fixed", "temperature": 1.2},
    "A": {"kind": "sequence", "temperatures": [1.2, 0.95, 0.7, 0.45, 0.2]},
    "C": {"kind": "sequence", "temperatures": [1.2, 0.2, 1.2, 0.2, 0.2]},
    "MTX": {"kind": "multi", "temperatures": [0.2, 0.7, 0.7, 1.2]},
    "E": {
        "kind": "adaptive",
        "initial_temperature": 1.0,
        "minimum_temperature": 0.2,
        "maximum_temperature": 1.2,
        "improvement_step": -0.2,
        "stagnation_step": 0.3,
    },
}

_ANALYSIS_PROVIDER_CONTRACTS: dict[str, dict[str, str]] = {
    PILOT_OFFICIAL_PROVIDER: {
        "request_model": PILOT_MODEL,
        "expected_response_model": PILOT_MODEL,
        "cache_mode": "complete",
        "prompt_cache_capability": "hit_and_miss_token_counts_reported",
        "prompt_cache_requirement": "complete_accounting_for_every_call",
        "fingerprint_mode": "stable",
        "fingerprint_capability": "system_fingerprint_reported",
        "fingerprint_requirement": "nonempty_and_stable_for_every_call",
        "endpoint_binding": "runtime_credential_sha256_audit_only",
    },
    PILOT_VOLCENGINE_PROVIDER: {
        "request_model": PILOT_MODEL,
        "expected_response_model": PILOT_VOLCENGINE_RESPONSE_MODEL,
        "cache_mode": "unavailable",
        "prompt_cache_capability": (
            "hit_and_miss_token_counts_unavailable_after_adapter_normalization"
        ),
        "prompt_cache_requirement": (
            "both_fields_null_or_omitted_after_adapter_normalization_for_every_call"
        ),
        "fingerprint_mode": "unavailable",
        "fingerprint_capability": (
            "system_fingerprint_unavailable_after_adapter_normalization"
        ),
        "fingerprint_requirement": (
            "null_or_omitted_after_adapter_normalization_for_every_call"
        ),
        "endpoint_binding": "fixed_exact_after_removing_trailing_slashes",
        "endpoint_sha256": hashlib.sha256(
            PILOT_VOLCENGINE_ENDPOINT.encode("utf-8")
        ).hexdigest(),
    },
}


class PilotAnalysisError(ValueError):
    """Raised when an artifact is structurally incapable of analysis."""


def _expected_config_hash(provider: str) -> str:
    config = {
        "schema_version": 1,
        "status": "development-only",
        "experiment": "adaptive-entropy-scheduling",
        "worlds": [
            {"seed": seed, "depth": depth} for seed, depth in PILOT_WORLDS
        ],
        "episode": _PILOT_EPISODE,
        "arms": _PILOT_ARM_CONFIGS,
        "model": {
            "provider": provider,
            "name": PILOT_MODEL,
            "snapshot": None,
            "structured_output": True,
        },
    }
    encoded = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotAnalysisError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PilotAnalysisError(f"{field} must be an array")
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PilotAnalysisError(f"{field} must be an integer >= {minimum}")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PilotAnalysisError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise PilotAnalysisError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise PilotAnalysisError(f"{field} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise PilotAnalysisError(f"{field} must be <= {maximum}")
    return result


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _provider_declaration_issues(
    source: Mapping[str, Any],
    *,
    provider_profile: str,
    contract: Mapping[str, str],
) -> list[str]:
    issues: list[str] = []
    declaration = source.get("provider_contract")
    if not isinstance(declaration, Mapping):
        return ["provider contract declaration is missing"]
    expected_scalars = {
        "profile": provider_profile,
        "request_model": contract["request_model"],
        "expected_response_model": contract["expected_response_model"],
        "contract_satisfied": True,
    }
    if any(declaration.get(key) != value for key, value in expected_scalars.items()):
        issues.append("provider contract identity or satisfaction declaration drifted")
    capability = declaration.get("capability_contract")
    if not isinstance(capability, Mapping):
        issues.append("provider capability contract declaration is missing")
    else:
        expected_capabilities = {
            "prompt_cache_usage": {
                "observed_capability": contract["prompt_cache_capability"],
                "pass_requirement": contract["prompt_cache_requirement"],
            },
            "system_fingerprint": {
                "observed_capability": contract["fingerprint_capability"],
                "pass_requirement": contract["fingerprint_requirement"],
            },
        }
        if capability != expected_capabilities:
            issues.append("provider capability contract declaration drifted")
    endpoint = declaration.get("endpoint_contract")
    if not isinstance(endpoint, Mapping):
        issues.append("provider endpoint contract declaration is missing")
    else:
        if (
            endpoint.get("binding") != contract["endpoint_binding"]
            or endpoint.get("normalization") != "remove_trailing_slashes_only"
            or endpoint.get("contract_satisfied") is not True
            or not _sha256_hex(endpoint.get("normalized_url_sha256"))
        ):
            issues.append("provider endpoint contract declaration drifted")
        elif (
            provider_profile == PILOT_VOLCENGINE_PROVIDER
            and endpoint.get("normalized_url_sha256")
            != contract["endpoint_sha256"]
        ):
            issues.append("Volcengine endpoint hash does not match the allowlist")
    return issues


def _candidate_engineering_issues(
    candidate: Mapping[str, Any],
    *,
    field: str,
    contract: Mapping[str, str],
) -> tuple[list[str], int]:
    issues: list[str] = []
    candidate_format = candidate.get("candidate_format")
    if candidate_format not in CANDIDATE_FORMATS:
        issues.append("one or more candidates have invalid candidate_format metadata")
    if candidate.get("provider_request_count") != 1:
        issues.append("one or more candidates do not prove one provider request")
    if candidate.get("seed_supported") is not False:
        issues.append("one or more candidates do not record seed_supported=false")
    if candidate.get("provider_model") != contract["expected_response_model"]:
        issues.append("one or more candidates have response-model drift")
    if candidate.get("finish_reason") != "stop":
        issues.append("one or more candidates have a non-stop finish reason")
    fingerprint = candidate.get("provider_fingerprint")
    if contract["fingerprint_mode"] == "stable":
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            issues.append("one or more candidates have no non-empty system fingerprint")
    elif fingerprint is not None:
        issues.append("one or more candidates unexpectedly expose a system fingerprint")

    input_tokens = _integer(candidate.get("input_tokens"), f"{field}.input_tokens")
    output_tokens = _integer(candidate.get("output_tokens"), f"{field}.output_tokens")
    if contract["cache_mode"] == "complete":
        hit_tokens = _integer(
            candidate.get("prompt_cache_hit_tokens"),
            f"{field}.prompt_cache_hit_tokens",
        )
        miss_tokens = _integer(
            candidate.get("prompt_cache_miss_tokens"),
            f"{field}.prompt_cache_miss_tokens",
        )
        if input_tokens != hit_tokens + miss_tokens:
            issues.append("one or more candidates have inconsistent cache accounting")
    elif (
        candidate.get("prompt_cache_hit_tokens") is not None
        or candidate.get("prompt_cache_miss_tokens") is not None
    ):
        issues.append("one or more candidates unexpectedly expose cache telemetry")
    if output_tokens > PILOT_MAX_OUTPUT_TOKENS:
        issues.append("one or more candidates exceed the frozen output-token cap")
    reasoning_tokens = candidate.get("reasoning_tokens")
    if reasoning_tokens not in {None, 0}:
        issues.append("one or more candidates report reasoning tokens with thinking disabled")
    _number(candidate.get("latency_ms"), f"{field}.latency_ms", minimum=0.0)
    if type(candidate.get("syntax_valid")) is not bool:
        raise PilotAnalysisError(f"{field}.syntax_valid must be a boolean")
    if type(candidate.get("runtime_valid")) is not bool:
        raise PilotAnalysisError(f"{field}.runtime_valid must be a boolean")
    return issues, input_tokens + output_tokens


def analyze_pilot(
    summary: Mapping[str, Any],
    *,
    source_result_sha256: str | None = None,
) -> dict[str, Any]:
    """Apply the frozen development stop rule to one complete pilot summary."""

    source = _mapping(summary, "summary")
    if source_result_sha256 is not None and (
        len(source_result_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_result_sha256)
    ):
        raise PilotAnalysisError("source_result_sha256 must be lowercase SHA-256 hex")
    raw_worlds = _sequence(source.get("worlds"), "summary.worlds")
    raw_runs = _sequence(source.get("runs"), "summary.runs")
    if len(raw_worlds) != len(PILOT_WORLDS):
        raise PilotAnalysisError(
            f"pilot requires {len(PILOT_WORLDS)} worlds; received {len(raw_worlds)}"
        )
    if len(raw_runs) != EXPECTED_RUNS:
        raise PilotAnalysisError(
            f"pilot requires {EXPECTED_RUNS} runs; received {len(raw_runs)}"
        )

    observed_worlds: list[tuple[int, int]] = []
    for index, raw_world in enumerate(raw_worlds):
        world = _mapping(raw_world, f"summary.worlds[{index}]")
        observed_worlds.append(
            (
                _integer(world.get("seed"), f"summary.worlds[{index}].seed"),
                _integer(world.get("depth"), f"summary.worlds[{index}].depth"),
            )
        )
    if tuple(observed_worlds) != PILOT_WORLDS:
        raise PilotAnalysisError("pilot world seeds/depths drifted from the frozen config")

    engineering_issues: list[str] = []
    model_summary = _mapping(source.get("model"), "summary.model")
    configured = _mapping(
        model_summary.get("configured"),
        "summary.model.configured",
    )
    configured_provider = configured.get("provider")
    provider_profile_known = configured_provider in _ANALYSIS_PROVIDER_CONTRACTS
    if not provider_profile_known:
        engineering_issues.append("configured provider profile is not allowlisted")
        provider_profile = str(configured_provider)
        # Continue a finite structural audit without allowing the unknown
        # profile to relax any official-provider requirement.
        contract = _ANALYSIS_PROVIDER_CONTRACTS[PILOT_OFFICIAL_PROVIDER]
    else:
        provider_profile = str(configured_provider)
        contract = _ANALYSIS_PROVIDER_CONTRACTS[provider_profile]
    expected_configured = {
        "provider": provider_profile,
        "name": contract["request_model"],
        "snapshot": None,
        "structured_output": True,
    }
    if configured != expected_configured:
        engineering_issues.append("configured model identity drifted")
    if provider_profile in _ANALYSIS_PROVIDER_CONTRACTS:
        if source.get("config_hash") != _expected_config_hash(provider_profile):
            engineering_issues.append("source config hash drifted")
        engineering_issues.extend(
            _provider_declaration_issues(
                source,
                provider_profile=provider_profile,
                contract=contract,
            )
        )
    if source.get("schema_version") != 1:
        engineering_issues.append("source schema version drifted")
    if source.get("config_status") != "development-only":
        engineering_issues.append("source config status drifted")
    if source.get("mode") != "development-pilot-live":
        engineering_issues.append("source mode is not development-pilot-live")
    if source.get("evidence") is not False or source.get("evidence_scope") != "non-evidence":
        engineering_issues.append("development pilot evidence scope drifted")
    if source.get("execution_contract") != {
        "logical_calls": EXPECTED_CALLS,
        "runs": EXPECTED_RUNS,
        "max_output_tokens_per_call": PILOT_MAX_OUTPUT_TOKENS,
        "provider_retries": 0,
        "resume_supported": False,
        "private_test_evaluation": (
            "globally_delayed_until_all_generation_calls_completed"
        ),
    }:
        engineering_issues.append("execution/no-resume/private-test contract drifted")
    if not isinstance(source.get("provenance"), Mapping):
        engineering_issues.append("source provenance manifest is missing")

    by_arm_runs: dict[str, list[Mapping[str, Any]]] = {
        arm: [] for arm in PILOT_ARMS
    }
    seen_pairs: set[tuple[int, str]] = set()
    all_candidates: list[Mapping[str, Any]] = []
    by_arm_candidates: dict[str, list[Mapping[str, Any]]] = {
        arm: [] for arm in PILOT_ARMS
    }
    test_scores: dict[str, list[float]] = {arm: [] for arm in PILOT_ARMS}
    unique_yields: dict[str, dict[str, list[float]]] = {
        arm: {"canonical": [], "behavior": []} for arm in PILOT_ARMS
    }
    token_totals: dict[str, int] = {arm: 0 for arm in PILOT_ARMS}
    fingerprints: set[str] = set()

    for run_index, raw_run in enumerate(raw_runs):
        run = _mapping(raw_run, f"summary.runs[{run_index}]")
        arm = run.get("arm_id")
        if arm not in by_arm_runs:
            raise PilotAnalysisError(f"summary.runs[{run_index}] has unknown arm")
        world = _mapping(run.get("world"), f"summary.runs[{run_index}].world")
        seed = _integer(world.get("seed"), f"summary.runs[{run_index}].world.seed")
        pair = (seed, str(arm))
        if pair in seen_pairs:
            raise PilotAnalysisError(f"duplicate world/arm run: {pair}")
        seen_pairs.add(pair)
        by_arm_runs[str(arm)].append(run)

        budget = _mapping(run.get("budget"), f"summary.runs[{run_index}].budget")
        if (
            budget.get("generation_calls_planned") != CALLS_PER_RUN
            or budget.get("generation_calls_completed") != CALLS_PER_RUN
            or budget.get("max_output_tokens_per_call")
            != PILOT_MAX_OUTPUT_TOKENS
            or budget.get("max_output_tokens_planned")
            != CALLS_PER_RUN * PILOT_MAX_OUTPUT_TOKENS
            or budget.get("max_output_tokens_completed_ceiling")
            != CALLS_PER_RUN * PILOT_MAX_OUTPUT_TOKENS
            or budget.get("provider_requests") != CALLS_PER_RUN
            or budget.get("retry_count") != 0
            or budget.get("actual_usage_available") is not True
            or budget.get("final_test_points_planned") in {None, 0}
            or budget.get("final_test_points_evaluated")
            != budget.get("final_test_points_planned")
        ):
            engineering_issues.append(
                f"run {run_index} has incomplete opportunity/usage accounting"
            )
        candidates = [
            _mapping(value, f"summary.runs[{run_index}].candidates[{candidate_index}]")
            for candidate_index, value in enumerate(
                _sequence(run.get("candidates"), f"summary.runs[{run_index}].candidates")
            )
        ]
        if len(candidates) != CALLS_PER_RUN:
            engineering_issues.append(f"run {run_index} does not contain 20 candidates")
        all_candidates.extend(candidates)
        by_arm_candidates[str(arm)].extend(candidates)

        valid = [
            candidate
            for candidate in candidates
            if candidate.get("syntax_valid") is True
            and candidate.get("runtime_valid") is True
        ]
        canonical = {
            value
            for candidate in valid
            if isinstance((value := candidate.get("canonical_hash")), str) and value
        }
        behavior = {
            value
            for candidate in valid
            if isinstance((value := candidate.get("behavior_hash")), str) and value
        }
        if any(
            not isinstance(candidate.get("canonical_hash"), str)
            or not candidate.get("canonical_hash")
            or not isinstance(candidate.get("behavior_hash"), str)
            or not candidate.get("behavior_hash")
            for candidate in valid
        ):
            engineering_issues.append(f"run {run_index} has valid candidates without hashes")
        unique_yields[str(arm)]["canonical"].append(
            _rate(len(canonical), CALLS_PER_RUN)
        )
        unique_yields[str(arm)]["behavior"].append(
            _rate(len(behavior), CALLS_PER_RUN)
        )

        final_test = _mapping(
            run.get("final_test"), f"summary.runs[{run_index}].final_test"
        )
        if final_test.get("evaluated") is not True:
            engineering_issues.append(f"run {run_index} has no delayed private-test result")
        test_scores[str(arm)].append(
            _number(
                final_test.get("accuracy"),
                f"summary.runs[{run_index}].final_test.accuracy",
                minimum=0.0,
                maximum=1.0,
            )
        )

        for candidate_index, candidate in enumerate(candidates):
            field = f"summary.runs[{run_index}].candidates[{candidate_index}]"
            expected_round, expected_candidate = divmod(candidate_index, 4)
            if (
                candidate.get("round_index") != expected_round
                or candidate.get("candidate_index") != expected_candidate
            ):
                engineering_issues.append("one or more candidate slot coordinates drifted")
            candidate_issues, billed = _candidate_engineering_issues(
                candidate,
                field=field,
                contract=contract,
            )
            engineering_issues.extend(candidate_issues)
            token_totals[str(arm)] += billed
            fingerprint = candidate.get("provider_fingerprint")
            if isinstance(fingerprint, str) and fingerprint.strip():
                fingerprints.add(fingerprint)

        run_input = sum(int(candidate["input_tokens"]) for candidate in candidates)
        run_output = sum(int(candidate["output_tokens"]) for candidate in candidates)
        run_latency = sum(float(candidate["latency_ms"]) for candidate in candidates)
        if (
            budget.get("actual_input_tokens") != run_input
            or budget.get("actual_output_tokens") != run_output
            or budget.get("actual_billed_tokens") != run_input + run_output
            or not isinstance(budget.get("latency_ms_total"), (int, float))
            or isinstance(budget.get("latency_ms_total"), bool)
            or not math.isclose(
                float(budget.get("latency_ms_total")),
                run_latency,
                rel_tol=0.0,
                abs_tol=1e-8,
            )
            or not isinstance(budget.get("latency_ms_mean"), (int, float))
            or isinstance(budget.get("latency_ms_mean"), bool)
            or not math.isclose(
                float(budget.get("latency_ms_mean")),
                run_latency / len(candidates) if candidates else 0.0,
                rel_tol=0.0,
                abs_tol=1e-8,
            )
        ):
            engineering_issues.append(
                f"run {run_index} has inconsistent token or latency aggregates"
            )
        if contract["cache_mode"] == "complete":
            expected_hit = sum(
                int(candidate["prompt_cache_hit_tokens"])
                for candidate in candidates
            )
            expected_miss = sum(
                int(candidate["prompt_cache_miss_tokens"])
                for candidate in candidates
            )
            if (
                budget.get("prompt_cache_hit_tokens") != expected_hit
                or budget.get("prompt_cache_miss_tokens") != expected_miss
            ):
                engineering_issues.append(
                    f"run {run_index} has inconsistent cache aggregates"
                )
        if budget.get("reasoning_tokens") not in {None, 0}:
            engineering_issues.append(f"run {run_index} has reasoning-token drift")

    expected_pairs = {
        (seed, arm) for seed, _depth in PILOT_WORLDS for arm in PILOT_ARMS
    }
    if seen_pairs != expected_pairs:
        raise PilotAnalysisError("pilot does not contain every frozen world/arm pair")
    if len(all_candidates) != EXPECTED_CALLS:
        engineering_issues.append("global candidate ledger is not exactly 1120 calls")
    if contract["fingerprint_mode"] == "stable" and len(fingerprints) != 1:
        engineering_issues.append("system fingerprint was missing or changed during the pilot")
    if contract["fingerprint_mode"] == "unavailable" and fingerprints:
        engineering_issues.append("system fingerprint unexpectedly appeared during the pilot")

    top_budget = _mapping(source.get("budget"), "summary.budget")
    if any(
        (
            top_budget.get("generation_calls_planned") != EXPECTED_CALLS,
            top_budget.get("generation_calls_completed") != EXPECTED_CALLS,
            top_budget.get("provider_requests") != EXPECTED_CALLS,
            top_budget.get("retry_count") != 0,
            top_budget.get("run_count") != EXPECTED_RUNS,
            top_budget.get("max_output_tokens_planned")
            != EXPECTED_CALLS * PILOT_MAX_OUTPUT_TOKENS,
            top_budget.get("max_output_tokens_completed_ceiling")
            != EXPECTED_CALLS * PILOT_MAX_OUTPUT_TOKENS,
            top_budget.get("actual_usage_available") is not True,
        )
    ):
        engineering_issues.append("global budget does not prove 1120 one-attempt responses")

    global_input = sum(int(candidate["input_tokens"]) for candidate in all_candidates)
    global_output = sum(int(candidate["output_tokens"]) for candidate in all_candidates)
    global_latency = sum(float(candidate["latency_ms"]) for candidate in all_candidates)
    if (
        top_budget.get("actual_input_tokens") != global_input
        or top_budget.get("actual_output_tokens") != global_output
        or top_budget.get("actual_billed_tokens") != global_input + global_output
        or not isinstance(top_budget.get("latency_ms_total"), (int, float))
        or isinstance(top_budget.get("latency_ms_total"), bool)
        or not math.isclose(
            float(top_budget.get("latency_ms_total")),
            global_latency,
            rel_tol=0.0,
            abs_tol=1e-7,
        )
    ):
        engineering_issues.append("global token or latency aggregates drifted")
    if top_budget.get("reasoning_tokens") not in {None, 0}:
        engineering_issues.append("global reasoning-token accounting drifted")
    if contract["cache_mode"] == "complete":
        global_hit = sum(
            int(candidate["prompt_cache_hit_tokens"])
            for candidate in all_candidates
        )
        global_miss = sum(
            int(candidate["prompt_cache_miss_tokens"])
            for candidate in all_candidates
        )
        if (
            top_budget.get("prompt_cache_hit_tokens") != global_hit
            or top_budget.get("prompt_cache_miss_tokens") != global_miss
        ):
            engineering_issues.append("global cache aggregates drifted")

    expected_response_models = [contract["expected_response_model"]]
    if model_summary.get("observed_response_models") != expected_response_models:
        engineering_issues.append("observed response-model ledger drifted")
    expected_fingerprint_ledger = sorted(fingerprints)
    if model_summary.get("observed_system_fingerprints") != expected_fingerprint_ledger:
        engineering_issues.append("observed system-fingerprint ledger drifted")
    if model_summary.get("finish_reason_counts") != {"stop": EXPECTED_CALLS}:
        engineering_issues.append("finish-reason ledger drifted")
    if contract["cache_mode"] == "unavailable":
        if (
            top_budget.get("prompt_cache_hit_tokens") is not None
            or top_budget.get("prompt_cache_miss_tokens") is not None
            or any(
                _mapping(run.get("budget"), "run.budget").get(
                    "prompt_cache_hit_tokens"
                )
                is not None
                or _mapping(run.get("budget"), "run.budget").get(
                    "prompt_cache_miss_tokens"
                )
                is not None
                for run in raw_runs
            )
        ):
            engineering_issues.append("cache telemetry unexpectedly appeared in aggregates")

    schema_counts = {
        arm: sum(
            candidate.get("candidate_format") == "json_expression"
            for candidate in candidates
        )
        for arm, candidates in by_arm_candidates.items()
    }
    schema_rates = {
        arm: _rate(schema_counts[arm], len(by_arm_candidates[arm]))
        for arm in PILOT_ARMS
    }
    overall_schema_count = sum(schema_counts.values())
    overall_schema_rate = _rate(overall_schema_count, EXPECTED_CALLS)
    format_passed = (
        overall_schema_rate >= OVERALL_SCHEMA_THRESHOLD
        and all(rate >= PER_ARM_SCHEMA_THRESHOLD for rate in schema_rates.values())
    )

    mean_yields = {
        arm: {
            kind: sum(values) / len(values) if values else 0.0
            for kind, values in kinds.items()
        }
        for arm, kinds in unique_yields.items()
    }
    manipulation = {
        "H_minus_L_unique_canonical_per_call": (
            mean_yields["H"]["canonical"] - mean_yields["L"]["canonical"]
        ),
        "H_minus_L_unique_behavior_per_call": (
            mean_yields["H"]["behavior"] - mean_yields["L"]["behavior"]
        ),
    }
    manipulation_passed = all(value > 0.0 for value in manipulation.values())

    mean_test_accuracy = {
        arm: sum(values) / len(values) if values else 0.0
        for arm, values in test_scores.items()
    }
    strongest_value = max(mean_test_accuracy[arm] for arm in PILOT_COMPARATORS)
    strongest_comparators = [
        arm
        for arm in PILOT_COMPARATORS
        if math.isclose(mean_test_accuracy[arm], strongest_value, abs_tol=1e-12)
    ]
    adaptive_delta = mean_test_accuracy["E"] - strongest_value

    mean_billed_tokens = {
        arm: token_totals[arm] / len(by_arm_candidates[arm])
        for arm in PILOT_ARMS
    }
    token_values = list(mean_billed_tokens.values())
    token_relative_range = (
        (max(token_values) - min(token_values)) / min(token_values)
        if token_values and min(token_values) > 0
        else None
    )
    token_equivalence_passed = bool(
        token_relative_range is not None
        and token_relative_range <= TOKEN_EQUIVALENCE_THRESHOLD
    )
    strongest_comparator_token_floor = min(
        mean_billed_tokens[arm] for arm in strongest_comparators
    )
    adaptive_token_ratio = (
        mean_billed_tokens["E"] / strongest_comparator_token_floor
        if strongest_comparator_token_floor > 0
        else None
    )
    if adaptive_delta > 0 and mean_billed_tokens["E"] <= strongest_comparator_token_floor:
        resource_pareto_status = "E_accuracy_dominates_without_more_tokens"
    elif adaptive_delta <= 0 and mean_billed_tokens["E"] >= strongest_comparator_token_floor:
        resource_pareto_status = "E_is_accuracy_resource_dominated_or_tied"
    else:
        resource_pareto_status = "accuracy_resource_tradeoff_unresolved"

    reasons: list[str] = []
    if engineering_issues:
        classification = "indeterminate"
        reasons.append("engineering/accounting gate failed")
    elif not format_passed:
        classification = "indeterminate"
        reasons.append("candidate-schema adherence gate failed")
    elif not manipulation_passed:
        classification = "indeterminate"
        reasons.append("H did not exceed L on both frozen per-call diversity yields")
    elif adaptive_delta + 1e-12 >= PRELIMINARY_POSITIVE_MARGIN:
        classification = "preliminary_positive"
        reasons.append("E cleared the frozen +0.05 hidden-test mean margin")
    elif adaptive_delta <= 0.0:
        classification = "current_operationalization_negative"
        reasons.append("E did not beat the strongest frozen nonadaptive comparator")
    else:
        classification = "indeterminate"
        reasons.append("E improved by more than zero but less than the frozen +0.05 margin")
    if not token_equivalence_passed:
        reasons.append(
            "realized-token equivalence failed; the call-matched result requires "
            "resource sensitivity"
        )

    # Keep failure reporting finite and compact; repeated candidate-level
    # violations are summarized by distinct safe messages.
    engineering_issues = sorted(set(engineering_issues))
    if not provider_profile_known:
        fingerprint_status = "failed"
        provenance_caveat = "The configured provider profile is not allowlisted."
    elif contract["fingerprint_mode"] == "unavailable":
        fingerprint_status = "capability_missing"
        provenance_caveat = (
            "The selected provider does not expose a system fingerprint after "
            "adapter normalization; the response alias and endpoint contract are "
            "audited, but backend-instance identity cannot be independently bound."
        )
    else:
        fingerprint_status = (
            "verified_stable" if len(fingerprints) == 1 else "failed"
        )
        provenance_caveat = None
    result = {
        "schema_version": PILOT_ANALYSIS_SCHEMA_VERSION,
        "kind": "eight-world-development-pilot-analysis",
        "classification": classification,
        "classification_scope": "preliminary-development-only",
        "classification_reasons": reasons,
        "stop_after_this_analysis": True,
        "thresholds": {
            "overall_schema_adherence": OVERALL_SCHEMA_THRESHOLD,
            "per_arm_schema_adherence": PER_ARM_SCHEMA_THRESHOLD,
            "preliminary_positive_margin": PRELIMINARY_POSITIVE_MARGIN,
            "realized_token_relative_range": TOKEN_EQUIVALENCE_THRESHOLD,
        },
        "engineering": {
            "passed": not engineering_issues,
            "issues": engineering_issues,
            "expected_calls": EXPECTED_CALLS,
            "observed_calls": len(all_candidates),
            "stable_system_fingerprint_count": len(fingerprints),
            "fingerprint_status": fingerprint_status,
            "provenance_caveat": provenance_caveat,
            "provider_profile": provider_profile,
        },
        "candidate_schema": {
            "passed": format_passed,
            "overall_count": overall_schema_count,
            "overall_rate": overall_schema_rate,
            "per_arm_count": schema_counts,
            "per_arm_rate": schema_rates,
        },
        "manipulation": {
            "passed": manipulation_passed,
            "mean_unique_yield_per_call_by_arm": mean_yields,
            **manipulation,
        },
        "performance": {
            "mean_hidden_test_accuracy_by_arm": mean_test_accuracy,
            "strongest_nonadaptive_comparators": strongest_comparators,
            "strongest_nonadaptive_mean": strongest_value,
            "E_minus_strongest_nonadaptive": adaptive_delta,
        },
        "resource_sensitivity": {
            "required": not token_equivalence_passed,
            "realized_token_equivalence_passed": token_equivalence_passed,
            "relative_range": token_relative_range,
            "mean_billed_tokens_per_call_by_arm": mean_billed_tokens,
            "strongest_comparator_token_floor_per_call": (
                strongest_comparator_token_floor
            ),
            "E_to_strongest_comparator_token_ratio": adaptive_token_ratio,
            "pareto_status": resource_pareto_status,
            "primary_estimand": "call-matched intention-to-treat",
            "actual_token_matched_claim_allowed": token_equivalence_passed,
        },
        "source": {
            "experiment": source.get("experiment"),
            "config_hash": source.get("config_hash"),
            "mode": source.get("mode"),
            "evidence": source.get("evidence") is True,
            "evidence_scope": source.get("evidence_scope"),
            "provider_profile": provider_profile,
            "result_sha256": source_result_sha256,
        },
        "caveat": (
            "Eight development worlds and a movable model alias support only a "
            "preliminary operational decision, not statistical confirmation or a "
            "claim about the broad RSI idea."
        ),
    }
    try:
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PilotAnalysisError("analysis result is not finite JSON") from exc
    return result


def write_new_json(path: str | Path, result: Mapping[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(encoded)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = args.input.read_bytes()
    try:
        source = _mapping(json.loads(payload.decode("utf-8")), "summary")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotAnalysisError("input must be one UTF-8 JSON artifact") from exc
    result = analyze_pilot(
        source,
        source_result_sha256=hashlib.sha256(payload).hexdigest(),
    )
    write_new_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


__all__ = [
    "EXPECTED_CALLS",
    "PILOT_ANALYSIS_SCHEMA_VERSION",
    "PILOT_ARMS",
    "PILOT_COMPARATORS",
    "PILOT_MODEL",
    "PILOT_OFFICIAL_PROVIDER",
    "PILOT_VOLCENGINE_ENDPOINT",
    "PILOT_VOLCENGINE_PROVIDER",
    "PILOT_VOLCENGINE_RESPONSE_MODEL",
    "PILOT_WORLDS",
    "PilotAnalysisError",
    "analyze_pilot",
    "main",
    "write_new_json",
]


if __name__ == "__main__":
    raise SystemExit(main())
