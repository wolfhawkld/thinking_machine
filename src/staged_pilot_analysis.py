"""Frozen descriptive analysis for staged development-pilot snapshots.

Execution protocol v2 preserves the original eight-world scientific grid but
allows cumulative, offline-only looks after 2, 4, and 8 sealed worlds.  The
first two looks are explicitly descriptive and can never emit the final pilot
labels.  Only the complete eight-world snapshot applies the unchanged
development decision rule from :mod:`src.pilot_analysis`.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .pilot_analysis import (
    OVERALL_SCHEMA_THRESHOLD,
    PER_ARM_SCHEMA_THRESHOLD,
    PILOT_ARMS,
    PILOT_COMPARATORS,
    PILOT_MAX_OUTPUT_TOKENS,
    PILOT_MODEL,
    PILOT_OFFICIAL_PROVIDER,
    PILOT_VOLCENGINE_PROVIDER,
    PILOT_WORLDS,
    PRELIMINARY_POSITIVE_MARGIN,
    TOKEN_EQUIVALENCE_THRESHOLD,
    _ANALYSIS_PROVIDER_CONTRACTS,
    _expected_config_hash,
    _provider_declaration_issues,
)
from .runner import CANDIDATE_FORMATS


STAGED_ANALYSIS_SCHEMA_VERSION = 1
CALLS_PER_RUN = 20
RUNS_PER_WORLD = len(PILOT_ARMS)
CALLS_PER_WORLD = CALLS_PER_RUN * RUNS_PER_WORLD
STAGE_DEFINITIONS: dict[int, dict[str, Any]] = {
    2: {
        "stage_id": "S1",
        "included_world_indices": [0, 1],
        "new_world_indices": [0, 1],
    },
    4: {
        "stage_id": "S2",
        "included_world_indices": [0, 1, 2, 3],
        "new_world_indices": [2, 3],
    },
    8: {
        "stage_id": "S3",
        "included_world_indices": list(range(8)),
        "new_world_indices": [4, 5, 6, 7],
    },
}
PRIVATE_TEST_RELEASE_RULE = (
    "after_all_required_checkpoints_and_world_seals_verified"
)


class StagedPilotAnalysisError(ValueError):
    """Raised when a snapshot is structurally incapable of staged analysis."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StagedPilotAnalysisError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise StagedPilotAnalysisError(f"{field} must be an array")
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise StagedPilotAnalysisError(
            f"{field} must be an integer >= {minimum}"
        )
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StagedPilotAnalysisError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise StagedPilotAnalysisError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise StagedPilotAnalysisError(f"{field} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise StagedPilotAnalysisError(f"{field} must be <= {maximum}")
    return result


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _sha256_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _stage_definition(source: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    stage = _mapping(source.get("stage"), "snapshot.stage")
    world_count = _integer(
        stage.get("cumulative_world_count"),
        "snapshot.stage.cumulative_world_count",
        minimum=1,
    )
    if world_count not in STAGE_DEFINITIONS:
        raise StagedPilotAnalysisError(
            "staged snapshot cumulative_world_count must be one of 2, 4, or 8"
        )
    return world_count, STAGE_DEFINITIONS[world_count]


def _candidate_issues(
    candidate: Mapping[str, Any],
    *,
    field: str,
    contract: Mapping[str, str],
) -> tuple[list[str], int]:
    issues: list[str] = []
    if "candidate_expression" in candidate:
        issues.append("public snapshot contains a sealed candidate expression")
    if candidate.get("candidate_format") not in CANDIDATE_FORMATS:
        issues.append("candidate-format metadata is outside the closed set")
    if candidate.get("provider_request_count") != 1:
        issues.append("an accepted candidate does not prove one provider request")
    if candidate.get("seed_supported") is not False:
        issues.append("an accepted candidate does not record seed_supported=false")
    if candidate.get("provider_model") != contract["expected_response_model"]:
        issues.append("an accepted candidate has response-model drift")
    if candidate.get("finish_reason") != "stop":
        issues.append("an accepted candidate has a non-stop finish reason")
    input_tokens = _integer(candidate.get("input_tokens"), f"{field}.input_tokens")
    output_tokens = _integer(
        candidate.get("output_tokens"), f"{field}.output_tokens"
    )
    if output_tokens > PILOT_MAX_OUTPUT_TOKENS:
        issues.append("an accepted candidate exceeds the frozen output-token cap")
    if candidate.get("reasoning_tokens") not in {None, 0}:
        issues.append("an accepted candidate reports reasoning tokens")
    _number(candidate.get("latency_ms"), f"{field}.latency_ms", minimum=0.0)
    if type(candidate.get("syntax_valid")) is not bool:
        raise StagedPilotAnalysisError(f"{field}.syntax_valid must be a boolean")
    if type(candidate.get("runtime_valid")) is not bool:
        raise StagedPilotAnalysisError(f"{field}.runtime_valid must be a boolean")

    if contract["cache_mode"] == "complete":
        hit = _integer(
            candidate.get("prompt_cache_hit_tokens"),
            f"{field}.prompt_cache_hit_tokens",
        )
        miss = _integer(
            candidate.get("prompt_cache_miss_tokens"),
            f"{field}.prompt_cache_miss_tokens",
        )
        if input_tokens != hit + miss:
            issues.append("an accepted candidate has inconsistent cache accounting")
    elif (
        candidate.get("prompt_cache_hit_tokens") is not None
        or candidate.get("prompt_cache_miss_tokens") is not None
    ):
        issues.append("cache telemetry unexpectedly appeared")

    fingerprint = candidate.get("provider_fingerprint")
    if contract["fingerprint_mode"] == "stable":
        if not isinstance(fingerprint, str) or not fingerprint.strip():
            issues.append("an accepted candidate has no stable fingerprint record")
    elif fingerprint is not None:
        issues.append("a system fingerprint unexpectedly appeared")
    return issues, input_tokens + output_tokens


def _boundary_signal(
    *,
    engineering_passed: bool,
    schema_passed: bool,
    manipulation_passed: bool,
    adaptive_delta: float,
) -> str:
    if not engineering_passed:
        return "not_interpretable_engineering"
    if not schema_passed:
        return "not_interpretable_schema"
    if not manipulation_passed:
        return "manipulation_not_yet_supported"
    if adaptive_delta + 1e-12 >= PRELIMINARY_POSITIVE_MARGIN:
        return "promising_signal"
    if adaptive_delta <= 0.0:
        return "unfavorable_signal"
    return "weak_signal"


def _recoverability(
    *,
    world_count: int,
    schema_counts: Mapping[str, int],
    overall_schema_count: int,
    canonical_sums: Mapping[str, float],
    behavior_sums: Mapping[str, float],
    accuracy_sums: Mapping[str, float],
) -> dict[str, bool]:
    remaining_worlds = len(PILOT_WORLDS) - world_count
    final_calls = len(PILOT_WORLDS) * CALLS_PER_WORLD
    final_calls_per_arm = len(PILOT_WORLDS) * CALLS_PER_RUN
    overall_best = overall_schema_count + remaining_worlds * CALLS_PER_WORLD
    per_arm_recoverable = all(
        schema_counts[arm] + remaining_worlds * CALLS_PER_RUN
        >= PER_ARM_SCHEMA_THRESHOLD * final_calls_per_arm
        for arm in PILOT_ARMS
    )
    schema_recoverable = (
        overall_best >= OVERALL_SCHEMA_THRESHOLD * final_calls
        and per_arm_recoverable
    )
    canonical_recoverable = (
        canonical_sums["H"] - canonical_sums["L"] + remaining_worlds > 0.0
    )
    behavior_recoverable = (
        behavior_sums["H"] - behavior_sums["L"] + remaining_worlds > 0.0
    )
    best_possible_e = (accuracy_sums["E"] + remaining_worlds) / len(
        PILOT_WORLDS
    )
    comparator_floor = max(
        accuracy_sums[arm] / len(PILOT_WORLDS) for arm in PILOT_COMPARATORS
    )
    return {
        "final_schema_gate_recoverable": schema_recoverable,
        "final_manipulation_gate_recoverable": (
            canonical_recoverable and behavior_recoverable
        ),
        "positive_margin_recoverable": (
            best_possible_e - comparator_floor + 1e-12
            >= PRELIMINARY_POSITIVE_MARGIN
        ),
    }


def analyze_staged_snapshot(
    summary: Mapping[str, Any],
    *,
    source_result_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate and analyze one cumulative 2/4/8-world snapshot."""

    source = _mapping(summary, "snapshot")
    if source_result_sha256 is not None and not _sha256_hex(source_result_sha256):
        raise StagedPilotAnalysisError(
            "source_result_sha256 must be lowercase SHA-256 hex"
        )
    world_count, definition = _stage_definition(source)
    stage = _mapping(source.get("stage"), "snapshot.stage")
    expected_runs = world_count * RUNS_PER_WORLD
    expected_calls = world_count * CALLS_PER_WORLD
    final_eligible = world_count == len(PILOT_WORLDS)

    engineering_issues: list[str] = []
    expected_stage = {
        "stage_id": definition["stage_id"],
        "included_world_indices": definition["included_world_indices"],
        "required_checkpoint_count": expected_runs,
        "required_world_seal_count": world_count,
        "final_classification_eligible": final_eligible,
        "private_test_release_rule": PRIVATE_TEST_RELEASE_RULE,
    }
    if any(stage.get(key) != value for key, value in expected_stage.items()):
        engineering_issues.append("stage declaration drifted from the 2/4/8 plan")
    observed_new_worlds = stage.get("new_world_indices")
    if observed_new_worlds is not None and observed_new_worlds != definition[
        "new_world_indices"
    ]:
        engineering_issues.append("stage new-world declaration drifted")

    if source.get("kind") != "staged-development-pilot-snapshot":
        engineering_issues.append("snapshot kind drifted")
    if source.get("schema_version") != 1:
        engineering_issues.append("snapshot schema version drifted")
    if source.get("config_status") != "development-only":
        engineering_issues.append("snapshot config status drifted")
    if source.get("mode") != "staged-development-pilot-offline-finalized":
        engineering_issues.append("snapshot was not produced by the offline finalizer")
    if source.get("evidence") is not False or source.get("evidence_scope") != "non-evidence":
        engineering_issues.append("staged development evidence scope drifted")

    campaign = _mapping(source.get("campaign"), "snapshot.campaign")
    for key in (
        "manifest_sha256",
        "config_sha256",
        "source_manifest_sha256",
        "plan_sha256",
    ):
        if not _sha256_hex(campaign.get(key)):
            engineering_issues.append(f"campaign {key} is not a canonical SHA-256")
    if campaign.get("config_sha256") != source.get("config_hash"):
        engineering_issues.append("campaign and snapshot config hashes differ")

    model_summary = _mapping(source.get("model"), "snapshot.model")
    configured = _mapping(model_summary.get("configured"), "snapshot.model.configured")
    provider = configured.get("provider")
    if provider not in _ANALYSIS_PROVIDER_CONTRACTS:
        engineering_issues.append("configured provider profile is not allowlisted")
        provider_profile = str(provider)
        contract = _ANALYSIS_PROVIDER_CONTRACTS[PILOT_OFFICIAL_PROVIDER]
    else:
        provider_profile = str(provider)
        contract = _ANALYSIS_PROVIDER_CONTRACTS[provider_profile]
    expected_configured = {
        "provider": provider_profile,
        "name": PILOT_MODEL,
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

    audit = _mapping(source.get("execution_audit"), "snapshot.execution_audit")
    committed_calls = audit.get(
        "committed_scientific_calls",
        audit.get("accepted_logical_calls"),
    )
    if committed_calls != expected_calls:
        engineering_issues.append("execution audit has the wrong committed call count")
    if audit.get("legacy_234_attempt_imported") is not False:
        engineering_issues.append("historical partial attempt was not explicitly excluded")
    for key in (
        "abandoned_operational_calls",
        "discarded_operational_calls",
        "ambiguous_operational_calls",
    ):
        if key in audit and (type(audit[key]) is not int or audit[key] < 0):
            engineering_issues.append(f"execution audit field {key} is malformed")

    raw_worlds = _sequence(source.get("worlds"), "snapshot.worlds")
    raw_runs = _sequence(source.get("runs"), "snapshot.runs")
    if len(raw_worlds) != world_count:
        raise StagedPilotAnalysisError("snapshot has the wrong number of worlds")
    if len(raw_runs) != expected_runs:
        raise StagedPilotAnalysisError("snapshot has the wrong number of runs")

    expected_worlds = PILOT_WORLDS[:world_count]
    observed_world_indices: list[int] = []
    for index, raw_world in enumerate(raw_worlds):
        world = _mapping(raw_world, f"snapshot.worlds[{index}]")
        world_index = _integer(world.get("index", index), f"worlds[{index}].index")
        observed_world_indices.append(world_index)
        if (
            world_index != index
            or world.get("seed") != expected_worlds[index][0]
            or world.get("depth") != expected_worlds[index][1]
        ):
            engineering_issues.append("world prefix or global index drifted")
    if observed_world_indices != definition["included_world_indices"]:
        engineering_issues.append("snapshot is not the frozen cumulative world prefix")

    by_arm_candidates: dict[str, list[Mapping[str, Any]]] = {
        arm: [] for arm in PILOT_ARMS
    }
    test_scores: dict[str, list[float]] = {arm: [] for arm in PILOT_ARMS}
    yield_values: dict[str, dict[str, list[float]]] = {
        arm: {"canonical": [], "behavior": []} for arm in PILOT_ARMS
    }
    token_totals = {arm: 0 for arm in PILOT_ARMS}
    accuracy_by_world: dict[int, dict[str, float]] = {
        index: {} for index in range(world_count)
    }
    seen_pairs: set[tuple[int, str]] = set()
    all_candidates: list[Mapping[str, Any]] = []
    fingerprints: set[str] = set()

    for run_index, raw_run in enumerate(raw_runs):
        run = _mapping(raw_run, f"snapshot.runs[{run_index}]")
        arm = run.get("arm_id")
        if arm not in by_arm_candidates:
            raise StagedPilotAnalysisError(f"run {run_index} has unknown arm")
        world = _mapping(run.get("world"), f"snapshot.runs[{run_index}].world")
        world_index = _integer(
            world.get("index"), f"snapshot.runs[{run_index}].world.index"
        )
        if world_index >= world_count:
            raise StagedPilotAnalysisError("run references a world outside the snapshot")
        pair = (world_index, str(arm))
        if pair in seen_pairs:
            raise StagedPilotAnalysisError(f"duplicate world/arm run: {pair}")
        seen_pairs.add(pair)

        budget = _mapping(run.get("budget"), f"snapshot.runs[{run_index}].budget")
        if (
            budget.get("generation_calls_planned") != CALLS_PER_RUN
            or budget.get("generation_calls_completed") != CALLS_PER_RUN
            or budget.get("max_output_tokens_per_call") != PILOT_MAX_OUTPUT_TOKENS
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
            engineering_issues.append(f"run {run_index} accounting is incomplete")

        candidates = [
            _mapping(value, f"snapshot.runs[{run_index}].candidates[{candidate_index}]")
            for candidate_index, value in enumerate(
                _sequence(run.get("candidates"), f"snapshot.runs[{run_index}].candidates")
            )
        ]
        if len(candidates) != CALLS_PER_RUN:
            engineering_issues.append(f"run {run_index} does not contain 20 candidates")
        by_arm_candidates[str(arm)].extend(candidates)
        all_candidates.extend(candidates)

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
        yield_values[str(arm)]["canonical"].append(
            _rate(len(canonical), CALLS_PER_RUN)
        )
        yield_values[str(arm)]["behavior"].append(
            _rate(len(behavior), CALLS_PER_RUN)
        )

        final_test = _mapping(
            run.get("final_test"), f"snapshot.runs[{run_index}].final_test"
        )
        if final_test.get("evaluated") is not True:
            engineering_issues.append(f"run {run_index} has no released stage test")
        accuracy = _number(
            final_test.get("accuracy"),
            f"snapshot.runs[{run_index}].final_test.accuracy",
            minimum=0.0,
            maximum=1.0,
        )
        test_scores[str(arm)].append(accuracy)
        accuracy_by_world[world_index][str(arm)] = accuracy

        for candidate_index, candidate in enumerate(candidates):
            expected_round, expected_candidate = divmod(candidate_index, 4)
            if (
                candidate.get("round_index") != expected_round
                or candidate.get("candidate_index") != expected_candidate
            ):
                engineering_issues.append("candidate slot coordinates drifted")
            candidate_issues, billed = _candidate_issues(
                candidate,
                field=f"snapshot.runs[{run_index}].candidates[{candidate_index}]",
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
            or not math.isclose(
                float(budget.get("latency_ms_total", -1.0)),
                run_latency,
                rel_tol=0.0,
                abs_tol=1e-8,
            )
        ):
            engineering_issues.append(f"run {run_index} usage aggregate drifted")
        if contract["cache_mode"] == "unavailable" and (
            budget.get("prompt_cache_hit_tokens") is not None
            or budget.get("prompt_cache_miss_tokens") is not None
        ):
            engineering_issues.append(f"run {run_index} cache aggregate appeared")

    expected_pairs = {
        (world_index, arm)
        for world_index in range(world_count)
        for arm in PILOT_ARMS
    }
    if seen_pairs != expected_pairs:
        raise StagedPilotAnalysisError("snapshot lacks a frozen world/arm pair")
    if any(set(values) != set(PILOT_ARMS) for values in accuracy_by_world.values()):
        raise StagedPilotAnalysisError("one or more worlds lack paired arm results")
    if len(all_candidates) != expected_calls:
        engineering_issues.append("global candidate ledger has the wrong size")

    top_budget = _mapping(source.get("budget"), "snapshot.budget")
    if (
        top_budget.get("generation_calls_planned") != expected_calls
        or top_budget.get("generation_calls_completed") != expected_calls
        or top_budget.get("provider_requests") != expected_calls
        or top_budget.get("retry_count") != 0
        or top_budget.get("run_count") != expected_runs
        or top_budget.get("max_output_tokens_planned")
        != expected_calls * PILOT_MAX_OUTPUT_TOKENS
        or top_budget.get("max_output_tokens_completed_ceiling")
        != expected_calls * PILOT_MAX_OUTPUT_TOKENS
        or top_budget.get("actual_usage_available") is not True
    ):
        engineering_issues.append("global budget does not prove the stage grid")
    global_input = sum(int(candidate["input_tokens"]) for candidate in all_candidates)
    global_output = sum(int(candidate["output_tokens"]) for candidate in all_candidates)
    global_latency = sum(float(candidate["latency_ms"]) for candidate in all_candidates)
    if (
        top_budget.get("actual_input_tokens") != global_input
        or top_budget.get("actual_output_tokens") != global_output
        or top_budget.get("actual_billed_tokens") != global_input + global_output
        or not math.isclose(
            float(top_budget.get("latency_ms_total", -1.0)),
            global_latency,
            rel_tol=0.0,
            abs_tol=1e-7,
        )
    ):
        engineering_issues.append("global token or latency aggregates drifted")
    if contract["cache_mode"] == "unavailable" and (
        top_budget.get("prompt_cache_hit_tokens") is not None
        or top_budget.get("prompt_cache_miss_tokens") is not None
    ):
        engineering_issues.append("global cache aggregate appeared")

    if model_summary.get("observed_response_models") != [
        contract["expected_response_model"]
    ]:
        engineering_issues.append("observed response-model ledger drifted")
    expected_fingerprints = sorted(fingerprints)
    if model_summary.get("observed_system_fingerprints") != expected_fingerprints:
        engineering_issues.append("observed fingerprint ledger drifted")
    if model_summary.get("finish_reason_counts") != {"stop": expected_calls}:
        engineering_issues.append("finish-reason ledger drifted")
    if contract["fingerprint_mode"] == "stable" and len(fingerprints) != 1:
        engineering_issues.append("system fingerprint was missing or changed")
    if contract["fingerprint_mode"] == "unavailable" and fingerprints:
        engineering_issues.append("system fingerprint unexpectedly appeared")

    schema_counts = {
        arm: sum(
            candidate.get("candidate_format") == "json_expression"
            for candidate in candidates
        )
        for arm, candidates in by_arm_candidates.items()
    }
    schema_rates = {
        arm: _rate(schema_counts[arm], world_count * CALLS_PER_RUN)
        for arm in PILOT_ARMS
    }
    overall_schema_count = sum(schema_counts.values())
    overall_schema_rate = _rate(overall_schema_count, expected_calls)
    schema_passed = (
        overall_schema_rate >= OVERALL_SCHEMA_THRESHOLD
        and all(rate >= PER_ARM_SCHEMA_THRESHOLD for rate in schema_rates.values())
    )

    mean_yields = {
        arm: {
            kind: sum(values) / len(values) if values else 0.0
            for kind, values in kinds.items()
        }
        for arm, kinds in yield_values.items()
    }
    canonical_sums = {
        arm: sum(yield_values[arm]["canonical"]) for arm in PILOT_ARMS
    }
    behavior_sums = {
        arm: sum(yield_values[arm]["behavior"]) for arm in PILOT_ARMS
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

    accuracy_sums = {arm: sum(test_scores[arm]) for arm in PILOT_ARMS}
    mean_accuracy = {
        arm: accuracy_sums[arm] / world_count for arm in PILOT_ARMS
    }
    strongest_value = max(mean_accuracy[arm] for arm in PILOT_COMPARATORS)
    strongest = [
        arm
        for arm in PILOT_COMPARATORS
        if math.isclose(mean_accuracy[arm], strongest_value, abs_tol=1e-12)
    ]
    adaptive_delta = mean_accuracy["E"] - strongest_value

    mean_tokens = {
        arm: token_totals[arm] / len(by_arm_candidates[arm])
        for arm in PILOT_ARMS
    }
    token_values = list(mean_tokens.values())
    token_relative_range = (
        (max(token_values) - min(token_values)) / min(token_values)
        if token_values and min(token_values) > 0
        else None
    )
    token_equivalence_passed = bool(
        token_relative_range is not None
        and token_relative_range <= TOKEN_EQUIVALENCE_THRESHOLD
    )
    recovery_used = any(
        int(audit.get(key, 0)) > 0
        for key in (
            "abandoned_operational_calls",
            "discarded_operational_calls",
            "ambiguous_operational_calls",
        )
    ) or audit.get("recovery_used") is True
    actual_token_claim_allowed = token_equivalence_passed and not recovery_used

    batch_metrics: list[dict[str, Any]] = []
    for batch_start in range(0, world_count, 2):
        if batch_start + 1 >= world_count:
            break
        pair = [batch_start, batch_start + 1]
        batch_means = {
            arm: sum(accuracy_by_world[index][arm] for index in pair) / 2.0
            for arm in PILOT_ARMS
        }
        batch_metrics.append(
            {
                "batch_id": f"B{batch_start // 2 + 1}",
                "world_indices": pair,
                "mean_hidden_test_accuracy_by_arm": batch_means,
                "E_minus_each_frozen_comparator": {
                    arm: batch_means["E"] - batch_means[arm]
                    for arm in PILOT_COMPARATORS
                },
                "independent_confirmation": False,
            }
        )

    engineering_issues = sorted(set(engineering_issues))
    engineering_passed = not engineering_issues
    signal = _boundary_signal(
        engineering_passed=engineering_passed,
        schema_passed=schema_passed,
        manipulation_passed=manipulation_passed,
        adaptive_delta=adaptive_delta,
    )

    if final_eligible:
        if not engineering_passed:
            classification = "indeterminate"
            reasons = ["staged execution/accounting gate failed"]
        elif not schema_passed:
            classification = "indeterminate"
            reasons = ["candidate-schema adherence gate failed"]
        elif not manipulation_passed:
            classification = "indeterminate"
            reasons = ["H did not exceed L on both frozen diversity yields"]
        elif adaptive_delta + 1e-12 >= PRELIMINARY_POSITIVE_MARGIN:
            classification = "preliminary_positive"
            reasons = ["E cleared the frozen +0.05 hidden-test mean margin"]
        elif adaptive_delta <= 0.0:
            classification = "current_operationalization_negative"
            reasons = ["E did not beat the strongest frozen comparator"]
        else:
            classification = "indeterminate"
            reasons = ["E improved by more than zero but less than +0.05"]
        scope = "preliminary-development-only"
        core_status = "decided_for_current_operationalization"
        completion = "complete"
    else:
        classification = "interim_descriptive_only"
        reasons = [
            "2/4-world look is exploratory and cannot issue the final pilot label",
            f"current boundary signal: {signal}",
        ]
        scope = "exploratory-development-interim"
        core_status = "not_decided"
        completion = "in_progress"
    if not actual_token_claim_allowed:
        reasons.append(
            "actual-token matching is not claimable; report resource sensitivity"
        )

    recoverability = _recoverability(
        world_count=world_count,
        schema_counts=schema_counts,
        overall_schema_count=overall_schema_count,
        canonical_sums=canonical_sums,
        behavior_sums=behavior_sums,
        accuracy_sums=accuracy_sums,
    )
    if contract["fingerprint_mode"] == "unavailable":
        fingerprint_status = "capability_missing"
        provenance_caveat = (
            "The provider exposes no system fingerprint after adapter "
            "normalization; endpoint and response alias are audited, but "
            "backend identity cannot be independently bound across stages."
        )
    else:
        fingerprint_status = "verified_stable" if len(fingerprints) == 1 else "failed"
        provenance_caveat = None

    result = {
        "schema_version": STAGED_ANALYSIS_SCHEMA_VERSION,
        "kind": "staged-development-pilot-analysis",
        "classification": classification,
        "classification_scope": scope,
        "classification_reasons": reasons,
        "boundary_signal": None if final_eligible else signal,
        "final_classification_eligible": final_eligible,
        "pilot_completion_status": completion,
        "core_hypothesis_status": core_status,
        "optional_stopping_present": True,
        "stop_after_this_analysis": True,
        "confirmatory_data_accessed": False,
        "stage": {
            "stage_id": definition["stage_id"],
            "cumulative_world_count": world_count,
            "included_world_indices": definition["included_world_indices"],
            "new_world_indices": definition["new_world_indices"],
            "cumulative_logical_calls": expected_calls,
            "inference_scope": scope,
            "provisional_comparator_not_frozen": not final_eligible,
        },
        "thresholds": {
            "overall_schema_adherence": OVERALL_SCHEMA_THRESHOLD,
            "per_arm_schema_adherence": PER_ARM_SCHEMA_THRESHOLD,
            "reference_or_final_margin": PRELIMINARY_POSITIVE_MARGIN,
            "realized_token_relative_range": TOKEN_EQUIVALENCE_THRESHOLD,
        },
        "engineering": {
            "passed": engineering_passed,
            "issues": engineering_issues,
            "expected_calls": expected_calls,
            "observed_calls": len(all_candidates),
            "provider_profile": provider_profile,
            "fingerprint_status": fingerprint_status,
            "provenance_caveat": provenance_caveat,
            "legacy_partial_attempt_imported": (
                audit.get("legacy_234_attempt_imported") is True
            ),
        },
        "candidate_schema": {
            "passed_at_current_boundary": schema_passed,
            "overall_count": overall_schema_count,
            "overall_rate": overall_schema_rate,
            "per_arm_count": schema_counts,
            "per_arm_rate": schema_rates,
        },
        "manipulation": {
            "passed_at_current_boundary": manipulation_passed,
            "mean_unique_yield_per_call_by_arm": mean_yields,
            **manipulation,
        },
        "performance": {
            "mean_hidden_test_accuracy_by_arm": mean_accuracy,
            "strongest_nonadaptive_comparators": strongest,
            "strongest_nonadaptive_mean": strongest_value,
            "E_minus_strongest_nonadaptive": adaptive_delta,
            "comparator_is_provisional": not final_eligible,
            "E_minus_each_frozen_comparator": {
                arm: mean_accuracy["E"] - mean_accuracy[arm]
                for arm in PILOT_COMPARATORS
            },
        },
        "nonoverlapping_batch_diagnostics": batch_metrics,
        "recoverability": recoverability,
        "resource_sensitivity": {
            "required": not actual_token_claim_allowed,
            "realized_token_equivalence_passed": token_equivalence_passed,
            "relative_range": token_relative_range,
            "mean_billed_tokens_per_accepted_call_by_arm": mean_tokens,
            "recovery_used": recovery_used,
            "actual_token_matched_claim_allowed": actual_token_claim_allowed,
            "primary_estimand": (
                "first-complete-episode-under-frozen-recovery-policy"
                if recovery_used
                else "call-matched intention-to-treat"
            ),
        },
        "allowed_boundary_actions": [
            "continue_as_frozen",
            "stop_engineering",
            "stop_irrecoverable_futility",
            "stop_resource_futility",
            "stop_external_reason",
        ],
        "source": {
            "experiment": source.get("experiment"),
            "config_hash": source.get("config_hash"),
            "mode": source.get("mode"),
            "provider_profile": provider_profile,
            "result_sha256": source_result_sha256,
        },
        "caveat": (
            "Worlds are execution-independent but depth-stratified, cumulative "
            "looks overlap, and human continuation creates optional stopping. "
            "Interim signals are not independent replications or final evidence."
        ),
    }
    try:
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StagedPilotAnalysisError("analysis result is not finite JSON") from exc
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
        source = _mapping(json.loads(payload.decode("utf-8")), "snapshot")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StagedPilotAnalysisError("input must be one UTF-8 JSON artifact") from exc
    result = analyze_staged_snapshot(
        source,
        source_result_sha256=hashlib.sha256(payload).hexdigest(),
    )
    write_new_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


__all__ = [
    "PRIVATE_TEST_RELEASE_RULE",
    "STAGE_DEFINITIONS",
    "STAGED_ANALYSIS_SCHEMA_VERSION",
    "StagedPilotAnalysisError",
    "analyze_staged_snapshot",
    "main",
    "write_new_json",
]


if __name__ == "__main__":
    raise SystemExit(main())
