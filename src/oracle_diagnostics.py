"""Offline, post-outcome candidate-pool diagnostics for the staged pilot.

This module deliberately lives outside the frozen primary analyzer.  It reads
only committed campaign state, verifies and replays every S3 episode, and then
uses the private test to estimate an unattainable Oracle@20 ceiling.  It never
loads credentials or constructs a provider client.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Any

from .experiment import _run_summary
from .pilot_checkpoint import (
    CAMPAIGN_MANIFEST_NAME,
    atomic_publish_public_snapshot,
    canonical_json_bytes,
    checkpoint_path,
    load_campaign_manifest,
    load_shard_checkpoint,
    load_world_seal,
    sha256_bytes,
    sha256_json,
    world_seal_path,
)
from .runner import evaluate_episode_test
from .staged_pilot import (
    SHARDS_PER_WORLD,
    _replay_checkpoint,
    audit_campaign,
)


ORACLE_DIAGNOSTIC_SCHEMA_VERSION = 1
PILOT_ARMS = ("L", "M", "H", "A", "C", "MTX", "E")
PILOT_COMPARATORS = ("M", "A", "C", "MTX")
WORLD_COUNT = 8
RUN_COUNT = WORLD_COUNT * len(PILOT_ARMS)
CALLS_PER_RUN = 20
TEST_POINTS_PER_RUN = 64
PROBE_POINTS_PER_RUN = 12
REPLAY_CRITICAL_FILES = (
    "src/dsl.py",
    "src/prompts.py",
    "src/policies.py",
    "src/world_generator.py",
    "src/verifier.py",
    "src/runner.py",
    "src/experiment.py",
    "src/development_pilot.py",
    "src/pilot_checkpoint.py",
    "src/staged_pilot.py",
)


class OracleDiagnosticError(RuntimeError):
    """Raised when a diagnostic input is incomplete, unsafe, or inconsistent."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OracleDiagnosticError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise OracleDiagnosticError(f"{field} must be an array")
    return value


def _read_json_object(path: Path, field: str) -> tuple[bytes, Mapping[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise OracleDiagnosticError(f"{field} must be a regular file")
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OracleDiagnosticError(f"{field} must be UTF-8 JSON") from exc
    return payload, _mapping(value, field)


def _source_file_hashes(manifest_payload: Mapping[str, Any]) -> dict[str, str]:
    source_manifest = _mapping(
        manifest_payload.get("source_manifest"), "manifest.source_manifest"
    )
    entries = _sequence(source_manifest.get("files"), "source_manifest.files")
    frozen: dict[str, str] = {}
    for index, raw_entry in enumerate(entries):
        entry = _mapping(raw_entry, f"source_manifest.files[{index}]")
        path = entry.get("path")
        digest = entry.get("sha256")
        if isinstance(path, str) and isinstance(digest, str):
            frozen[path] = digest

    project_root = Path(__file__).resolve().parents[1]
    verified: dict[str, str] = {}
    for relative in REPLAY_CRITICAL_FILES:
        expected = frozen.get(relative)
        if expected is None:
            raise OracleDiagnosticError(
                f"frozen source manifest lacks replay-critical file {relative}"
            )
        actual = sha256_bytes((project_root / relative).read_bytes())
        if actual != expected:
            raise OracleDiagnosticError(
                f"replay-critical source drifted from campaign: {relative}"
            )
        verified[relative] = actual
    return verified


def _average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for offset in range(start, end):
            ranks[order[offset]] = rank
        start = end
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    centered_x = [value - mean_x for value in xs]
    centered_y = [value - mean_y for value in ys]
    denominator = math.sqrt(
        sum(value * value for value in centered_x)
        * sum(value * value for value in centered_y)
    )
    if denominator == 0.0:
        return None
    return sum(x * y for x, y in zip(centered_x, centered_y)) / denominator


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    return _pearson(_average_ranks(xs), _average_ranks(ys))


def _kendall_tau_b(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    concordant = 0
    discordant = 0
    ties_x_only = 0
    ties_y_only = 0
    for left in range(len(xs) - 1):
        for right in range(left + 1, len(xs)):
            dx = (xs[left] > xs[right]) - (xs[left] < xs[right])
            dy = (ys[left] > ys[right]) - (ys[left] < ys[right])
            if dx == 0 and dy == 0:
                continue
            if dx == 0:
                ties_x_only += 1
            elif dy == 0:
                ties_y_only += 1
            elif dx == dy:
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt(
        (concordant + discordant + ties_x_only)
        * (concordant + discordant + ties_y_only)
    )
    if denominator == 0.0:
        return None
    return (concordant - discordant) / denominator


def _deduplicate(
    candidates: Sequence[Mapping[str, Any]], key: str
) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    result: list[Mapping[str, Any]] = []
    for candidate in candidates:
        identity = str(candidate[key])
        if identity not in seen:
            seen.add(identity)
            result.append(candidate)
    return result


def _relationship(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    call_probe = [float(item["probe_correct"]) for item in candidates]
    call_test = [float(item["test_correct"]) for item in candidates]
    canonical = _deduplicate(candidates, "canonical_hash")
    behavior = _deduplicate(candidates, "behavior_hash")
    canonical_probe = [float(item["probe_correct"]) for item in canonical]
    canonical_test = [float(item["test_correct"]) for item in canonical]
    behavior_probe = [float(item["probe_correct"]) for item in behavior]
    behavior_test = [float(item["test_correct"]) for item in behavior]
    tau = _kendall_tau_b(behavior_probe, behavior_test)
    if len(behavior) < 2:
        undefined_reason = "fewer_than_two_unique_behaviors"
    elif tau is None:
        undefined_reason = "all_pairs_tied_on_probe_or_test"
    else:
        undefined_reason = None
    return {
        "primary_behavior_deduplicated": {
            "candidate_count": len(behavior),
            "kendall_tau_b": tau,
            "undefined_reason": undefined_reason,
        },
        "call_weighted_sensitivity": {
            "candidate_count": len(candidates),
            "pearson": _pearson(call_probe, call_test),
            "spearman": _spearman(call_probe, call_test),
        },
        "canonical_deduplicated_sensitivity": {
            "candidate_count": len(canonical),
            "pearson": _pearson(canonical_probe, canonical_test),
            "spearman": _spearman(canonical_probe, canonical_test),
        },
    }


def _mean(values: Sequence[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def _optional_summary(values: Sequence[float | None]) -> dict[str, Any]:
    defined = [float(value) for value in values if value is not None]
    return {
        "defined_run_count": len(defined),
        "mean": _mean(defined),
        "median": None if not defined else float(statistics.median(defined)),
    }


def _boolean_rate(values: Sequence[bool | None]) -> dict[str, Any]:
    defined = [value for value in values if value is not None]
    true_count = sum(value is True for value in defined)
    return {
        "true_count": true_count,
        "defined_run_count": len(defined),
        "rate": None if not defined else true_count / len(defined),
    }


def _count_summary(
    *,
    planned: int,
    outer_schema_valid: int,
    syntax_valid: int,
    runtime_valid: int,
    search_valid: int,
    failure_codes: Mapping[str, int],
) -> dict[str, Any]:
    invalid = planned - search_valid
    return {
        "planned_slots": planned,
        "outer_schema_valid_count": outer_schema_valid,
        "outer_schema_valid_rate": outer_schema_valid / planned,
        "syntax_valid_count": syntax_valid,
        "syntax_valid_rate": syntax_valid / planned,
        "runtime_valid_count": runtime_valid,
        "runtime_valid_rate": runtime_valid / planned,
        "runtime_only_failure_count": syntax_valid - search_valid,
        "search_valid_count": search_valid,
        "search_valid_rate": search_valid / planned,
        "search_invalid_count": invalid,
        "search_invalid_rate": invalid / planned,
        "failure_code_record_counts": dict(sorted(failure_codes.items())),
    }


def _run_diagnostic(
    *,
    result: Any,
    verifier: Any,
    summary_kwargs: Mapping[str, Any],
    snapshot_run: Mapping[str, Any],
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    records = [record for round_records in result.rounds for record in round_records]
    if len(records) != CALLS_PER_RUN:
        raise OracleDiagnosticError("replayed run does not contain 20 candidate slots")

    evaluated: list[dict[str, Any]] = []
    for record in records:
        if not (record.syntax_valid and record.runtime_valid):
            continue
        test_result = verifier.verify_test(
            record.candidate,
            result.world,
            counterexample_limit=0,
        )
        if test_result.syntax_valid is not True or test_result.total != TEST_POINTS_PER_RUN:
            raise OracleDiagnosticError("eligible candidate did not reproduce on test")
        test_score = float(test_result.score)
        test_correct = int(round(test_score * TEST_POINTS_PER_RUN))
        if not math.isclose(
            test_score,
            test_correct / TEST_POINTS_PER_RUN,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise OracleDiagnosticError("candidate test score is not an exact count")
        probe_correct = int(round(float(record.probe_score) * PROBE_POINTS_PER_RUN))
        if not math.isclose(
            float(record.probe_score),
            probe_correct / PROBE_POINTS_PER_RUN,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise OracleDiagnosticError("candidate probe score is not an exact count")
        evaluated.append(
            {
                "record": record,
                "probe_correct": probe_correct,
                "test_correct": test_correct,
                "test_runtime_valid": bool(test_result.runtime_valid),
                "canonical_hash": str(record.canonical_hash),
                "behavior_hash": str(record.behavior_hash),
            }
        )

    selected_test = evaluate_episode_test(result, verifier=verifier)
    result.final_test = selected_test
    reproduced = json.loads(
        canonical_json_bytes(_run_summary(result=result, **dict(summary_kwargs)))
    )
    for candidate in reproduced["candidates"]:
        candidate.pop("candidate_expression", None)
    if reproduced != snapshot_run:
        raise OracleDiagnosticError(
            "replayed selected test or public run disagrees with the S3 snapshot"
        )

    selected_metrics: Mapping[str, Any] | None = None
    if result.final_candidate is not None:
        selected_metrics = next(
            (
                item
                for item in evaluated
                if item["record"] is result.final_candidate
            ),
            None,
        )
        if selected_metrics is None:
            raise OracleDiagnosticError("selected candidate is not search-eligible")
    if bool(evaluated) != (selected_metrics is not None):
        raise OracleDiagnosticError("selection success disagrees with eligible pool")

    oracle_correct = (
        max(int(item["test_correct"]) for item in evaluated) if evaluated else None
    )
    selected_correct = (
        None if selected_metrics is None else int(selected_metrics["test_correct"])
    )
    if oracle_correct is not None and selected_correct is not None:
        if selected_correct > oracle_correct:
            raise OracleDiagnosticError("selected result exceeds its candidate oracle")
        regret_correct = oracle_correct - selected_correct
    else:
        regret_correct = None

    max_probe = (
        max(int(item["probe_correct"]) for item in evaluated) if evaluated else None
    )
    oracle_in_probe_tie = (
        None
        if oracle_correct is None
        else any(
            int(item["probe_correct"]) == max_probe
            and int(item["test_correct"]) == oracle_correct
            for item in evaluated
        )
    )
    failure_codes = Counter(
        code for record in records for code in tuple(record.failure_codes)
    )
    search_valid_count = len(evaluated)
    run = {
        "world_index": int(summary_kwargs["world_index"]),
        "world_depth": int(summary_kwargs["world_depth"]),
        "arm_id": str(summary_kwargs["arm_id"]),
        "planned_slots": CALLS_PER_RUN,
        "outer_schema_valid_count": sum(
            record.candidate_format == "json_expression" for record in records
        ),
        "syntax_valid_count": sum(record.syntax_valid for record in records),
        "runtime_valid_count": sum(record.runtime_valid for record in records),
        "search_valid_count": search_valid_count,
        "search_invalid_count": CALLS_PER_RUN - search_valid_count,
        "unique_canonical_count": len(
            {str(item["canonical_hash"]) for item in evaluated}
        ),
        "unique_behavior_count": len(
            {str(item["behavior_hash"]) for item in evaluated}
        ),
        "failure_code_record_counts": dict(sorted(failure_codes.items())),
        "selected": {
            "exists": selected_correct is not None,
            "observed_test_correct": selected_correct,
            "observed_test_accuracy": (
                None
                if selected_correct is None
                else selected_correct / TEST_POINTS_PER_RUN
            ),
            "terminal_zero_sensitivity_score": (
                0.0
                if selected_correct is None
                else selected_correct / TEST_POINTS_PER_RUN
            ),
        },
        "oracle_at_20": {
            "exists": oracle_correct is not None,
            "observed_test_correct": oracle_correct,
            "observed_test_accuracy": (
                None if oracle_correct is None else oracle_correct / TEST_POINTS_PER_RUN
            ),
            "terminal_zero_sensitivity_score": (
                0.0
                if oracle_correct is None
                else oracle_correct / TEST_POINTS_PER_RUN
            ),
            "tie_count_slots": (
                0
                if oracle_correct is None
                else sum(
                    int(item["test_correct"]) == oracle_correct for item in evaluated
                )
            ),
            "present_in_max_probe_tie_set": oracle_in_probe_tie,
        },
        "selection_regret": {
            "observed_test_correct": regret_correct,
            "observed_accuracy": (
                None
                if regret_correct is None
                else regret_correct / TEST_POINTS_PER_RUN
            ),
            "terminal_zero_sensitivity_score": (
                0.0
                if regret_correct is None
                else regret_correct / TEST_POINTS_PER_RUN
            ),
            "selected_is_oracle_tie": (
                None if regret_correct is None else regret_correct == 0
            ),
        },
        "probe_test_relationship": _relationship(evaluated),
    }
    private_runtime_failures = sum(
        item["test_runtime_valid"] is False for item in evaluated
    )
    run["private_test_runtime_failure_count"] = private_runtime_failures
    return run, evaluated


def _arm_summary(per_run: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm in PILOT_ARMS:
        runs = [run for run in per_run if run["arm_id"] == arm]
        if len(runs) != WORLD_COUNT:
            raise OracleDiagnosticError(f"arm {arm} does not contain eight runs")
        selected_zero = [float(run["selected"]["terminal_zero_sensitivity_score"]) for run in runs]
        oracle_zero = [
            float(run["oracle_at_20"]["terminal_zero_sensitivity_score"])
            for run in runs
        ]
        regret_zero = [
            float(run["selection_regret"]["terminal_zero_sensitivity_score"])
            for run in runs
        ]
        selected_observed = [
            run["selected"]["observed_test_accuracy"] for run in runs
        ]
        oracle_observed = [
            run["oracle_at_20"]["observed_test_accuracy"] for run in runs
        ]
        regret_observed = [
            run["selection_regret"]["observed_accuracy"] for run in runs
        ]
        behavior_tau = [
            run["probe_test_relationship"]["primary_behavior_deduplicated"][
                "kendall_tau_b"
            ]
            for run in runs
        ]
        call_pearson = [
            run["probe_test_relationship"]["call_weighted_sensitivity"]["pearson"]
            for run in runs
        ]
        call_spearman = [
            run["probe_test_relationship"]["call_weighted_sensitivity"]["spearman"]
            for run in runs
        ]
        canonical_pearson = [
            run["probe_test_relationship"]["canonical_deduplicated_sensitivity"][
                "pearson"
            ]
            for run in runs
        ]
        canonical_spearman = [
            run["probe_test_relationship"]["canonical_deduplicated_sensitivity"][
                "spearman"
            ]
            for run in runs
        ]
        selected_ties = [
            run["selection_regret"]["selected_is_oracle_tie"] for run in runs
        ]
        oracle_in_probe = [
            run["oracle_at_20"]["present_in_max_probe_tie_set"] for run in runs
        ]
        result[arm] = {
            "world_count": WORLD_COUNT,
            "planned_slots": WORLD_COUNT * CALLS_PER_RUN,
            "search_valid_count": sum(int(run["search_valid_count"]) for run in runs),
            "search_invalid_count": sum(
                int(run["search_invalid_count"]) for run in runs
            ),
            "selection_success_count": sum(run["selected"]["exists"] for run in runs),
            "selected_accuracy_observed_runs": _optional_summary(selected_observed),
            "oracle_at_20_accuracy_observed_runs": _optional_summary(oracle_observed),
            "selection_regret_observed_runs": _optional_summary(regret_observed),
            "mean_selected_terminal_zero_sensitivity": _mean(selected_zero),
            "mean_oracle_at_20_terminal_zero_sensitivity": _mean(oracle_zero),
            "mean_selection_regret_terminal_zero_sensitivity": _mean(regret_zero),
            "selected_terminal_zero_correct_total": int(
                round(sum(selected_zero) * TEST_POINTS_PER_RUN)
            ),
            "oracle_terminal_zero_correct_total": int(
                round(sum(oracle_zero) * TEST_POINTS_PER_RUN)
            ),
            "selected_is_oracle_tie_when_selected": _boolean_rate(
                selected_ties
            ),
            "oracle_present_in_max_probe_tie_when_oracle_exists": _boolean_rate(
                oracle_in_probe
            ),
            "probe_test_relationship": {
                "primary_behavior_deduplicated_kendall_tau_b": _optional_summary(
                    behavior_tau
                ),
                "call_weighted_pearson": _optional_summary(call_pearson),
                "call_weighted_spearman": _optional_summary(call_spearman),
                "canonical_deduplicated_pearson": _optional_summary(
                    canonical_pearson
                ),
                "canonical_deduplicated_spearman": _optional_summary(
                    canonical_spearman
                ),
            },
        }
    return result


def _paired_comparison(
    per_run: Sequence[Mapping[str, Any]], comparator: str
) -> dict[str, Any]:
    by_pair = {
        (int(run["world_index"]), str(run["arm_id"])): run for run in per_run
    }
    rows: list[dict[str, Any]] = []
    for world_index in range(WORLD_COUNT):
        adaptive = by_pair[(world_index, "E")]
        baseline = by_pair[(world_index, comparator)]
        selected_delta = (
            float(adaptive["selected"]["terminal_zero_sensitivity_score"])
            - float(baseline["selected"]["terminal_zero_sensitivity_score"])
        )
        oracle_delta = (
            float(adaptive["oracle_at_20"]["terminal_zero_sensitivity_score"])
            - float(baseline["oracle_at_20"]["terminal_zero_sensitivity_score"])
        )
        regret_delta = (
            float(
                adaptive["selection_regret"]["terminal_zero_sensitivity_score"]
            )
            - float(
                baseline["selection_regret"]["terminal_zero_sensitivity_score"]
            )
        )
        residual = selected_delta - (oracle_delta - regret_delta)
        if not math.isclose(residual, 0.0, rel_tol=0.0, abs_tol=1e-12):
            raise OracleDiagnosticError("selected/oracle/regret decomposition drifted")
        rows.append(
            {
                "world_index": world_index,
                "selected_accuracy_difference": selected_delta,
                "oracle_at_20_accuracy_difference": oracle_delta,
                "selection_regret_difference": regret_delta,
                "decomposition_residual": residual,
            }
        )
    return {
        "comparator": comparator,
        "per_world": rows,
        "mean_selected_accuracy_difference": _mean(
            [row["selected_accuracy_difference"] for row in rows]
        ),
        "mean_oracle_at_20_accuracy_difference": _mean(
            [row["oracle_at_20_accuracy_difference"] for row in rows]
        ),
        "mean_selection_regret_difference": _mean(
            [row["selection_regret_difference"] for row in rows]
        ),
    }


def _dsl_summary(
    per_run: Sequence[Mapping[str, Any]], candidate_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    def summarize_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        planned = sum(int(run["planned_slots"]) for run in runs)
        codes = Counter(
            {
                code: sum(
                    int(run["failure_code_record_counts"].get(code, 0))
                    for run in runs
                )
                for code in {
                    code
                    for run in runs
                    for code in run["failure_code_record_counts"]
                }
            }
        )
        return _count_summary(
            planned=planned,
            outer_schema_valid=sum(
                int(run["outer_schema_valid_count"]) for run in runs
            ),
            syntax_valid=sum(int(run["syntax_valid_count"]) for run in runs),
            runtime_valid=sum(int(run["runtime_valid_count"]) for run in runs),
            search_valid=sum(int(run["search_valid_count"]) for run in runs),
            failure_codes=codes,
        )

    by_arm = {
        arm: summarize_runs([run for run in per_run if run["arm_id"] == arm])
        for arm in PILOT_ARMS
    }
    by_world = [
        {
            "world_index": world_index,
            **summarize_runs(
                [run for run in per_run if run["world_index"] == world_index]
            ),
        }
        for world_index in range(WORLD_COUNT)
    ]
    world_arm = [
        {
            "world_index": int(run["world_index"]),
            "arm_id": str(run["arm_id"]),
            **summarize_runs([run]),
        }
        for run in per_run
    ]
    overall = summarize_runs(per_run)
    if int(overall["search_valid_count"]) != len(candidate_rows):
        raise OracleDiagnosticError("candidate validity aggregation drifted")
    invalid_total = int(overall["search_invalid_count"])
    for row in by_world:
        row["share_of_all_search_invalid"] = (
            0.0
            if invalid_total == 0
            else int(row["search_invalid_count"]) / invalid_total
        )
    worst = max(by_world, key=lambda row: int(row["search_invalid_count"]))
    remaining_invalid = invalid_total - int(worst["search_invalid_count"])
    remaining_planned = int(overall["planned_slots"]) - int(worst["planned_slots"])
    return {
        "eligibility_rule": "syntax_valid && runtime_valid",
        "overall": overall,
        "by_arm": by_arm,
        "by_world": by_world,
        "world_arm_matrix": world_arm,
        "concentration": {
            "worst_world_index": int(worst["world_index"]),
            "worst_world_search_invalid_count": int(worst["search_invalid_count"]),
            "worst_world_search_invalid_rate": float(worst["search_invalid_rate"]),
            "worst_world_share_of_all_search_invalid": float(
                worst["share_of_all_search_invalid"]
            ),
            "other_worlds_search_invalid_count": remaining_invalid,
            "other_worlds_search_invalid_rate": remaining_invalid
            / remaining_planned,
            "leave_worst_world_out_is_post_hoc": True,
        },
        "all_invalid_runs": [
            {
                "world_index": int(run["world_index"]),
                "arm_id": str(run["arm_id"]),
            }
            for run in per_run
            if int(run["search_valid_count"]) == 0
        ],
    }


def analyze_oracle_diagnostic(
    campaign_dir: str | Path,
    snapshot_path: str | Path,
) -> dict[str, Any]:
    """Verify a complete staged campaign and compute post-hoc diagnostics."""

    root = Path(campaign_dir)
    snapshot_file = Path(snapshot_path)
    snapshot_bytes, snapshot = _read_json_object(snapshot_file, "S3 snapshot")
    if "candidate_expression" in snapshot_bytes.decode("utf-8"):
        raise OracleDiagnosticError("public S3 snapshot contains candidate expressions")
    stage = _mapping(snapshot.get("stage"), "snapshot.stage")
    runs = _sequence(snapshot.get("runs"), "snapshot.runs")
    if (
        snapshot.get("kind") != "staged-development-pilot-snapshot"
        or stage.get("stage_id") != "S3"
        or stage.get("cumulative_world_count") != WORLD_COUNT
        or stage.get("final_classification_eligible") is not True
        or len(runs) != RUN_COUNT
    ):
        raise OracleDiagnosticError("diagnostic requires the complete S3 snapshot")

    manifest_path = root / CAMPAIGN_MANIFEST_NAME
    manifest_bytes = manifest_path.read_bytes()
    manifest = load_campaign_manifest(root)
    manifest_payload = _mapping(manifest.get("payload"), "campaign manifest payload")
    campaign = _mapping(snapshot.get("campaign"), "snapshot.campaign")
    if (
        campaign.get("manifest_sha256") != sha256_bytes(manifest_bytes)
        or campaign.get("config_sha256")
        != manifest_payload.get("scientific_config_sha256")
        or campaign.get("staged_config_sha256")
        != manifest_payload.get("staged_config_sha256")
        or campaign.get("source_manifest_sha256")
        != manifest_payload.get("source_manifest_sha256")
        or campaign.get("plan_sha256") != manifest_payload.get("plan_sha256")
    ):
        raise OracleDiagnosticError("S3 snapshot is not bound to this campaign")
    critical_hashes = _source_file_hashes(manifest_payload)
    state = audit_campaign(root, manifest=manifest, require_world_seals=True)
    if (
        state.get("committed_shard_count") != RUN_COUNT
        or state.get("committed_scientific_calls") != RUN_COUNT * CALLS_PER_RUN
        or state.get("sealed_world_indices") != list(range(WORLD_COUNT))
    ):
        raise OracleDiagnosticError("campaign has not reached the complete sealed S3 barrier")

    plan = _sequence(manifest_payload.get("plan"), "manifest.plan")
    if len(plan) != RUN_COUNT:
        raise OracleDiagnosticError("campaign plan does not contain 56 runs")
    checkpoint_binding: list[dict[str, Any]] = []
    seal_binding: list[dict[str, Any]] = []
    pending: list[tuple[Any, Any, Mapping[str, Any], Mapping[str, Any]]] = []
    scientific = _mapping(
        manifest_payload.get("scientific_config"), "manifest.scientific_config"
    )

    # Release barrier: load, verify, and replay every run before the first
    # candidate is evaluated on a private-test point.
    for shard_index in range(RUN_COUNT):
        checkpoint = load_shard_checkpoint(root, shard_index)
        checkpoint_file = checkpoint_path(root, shard_index)
        checkpoint_binding.append(
            {
                "shard_index": shard_index,
                "file_sha256": sha256_bytes(checkpoint_file.read_bytes()),
                "payload_sha256": checkpoint["payload_sha256"],
            }
        )
        replayed, verifier, summary_kwargs = _replay_checkpoint(
            checkpoint,
            scientific=scientific,
            plan_entry=_mapping(plan[shard_index], f"manifest.plan[{shard_index}]"),
        )
        pending.append(
            (
                replayed,
                verifier,
                summary_kwargs,
                _mapping(runs[shard_index], f"snapshot.runs[{shard_index}]"),
            )
        )
    for world_index in range(WORLD_COUNT):
        seal = load_world_seal(root, world_index)
        seal_file = world_seal_path(root, world_index)
        seal_binding.append(
            {
                "world_index": world_index,
                "file_sha256": sha256_bytes(seal_file.read_bytes()),
                "payload_sha256": seal["payload_sha256"],
            }
        )

    per_run: list[dict[str, Any]] = []
    candidate_rows: list[Mapping[str, Any]] = []
    for replayed, verifier, summary_kwargs, snapshot_run in pending:
        run, evaluated = _run_diagnostic(
            result=replayed,
            verifier=verifier,
            summary_kwargs=summary_kwargs,
            snapshot_run=snapshot_run,
        )
        per_run.append(run)
        candidate_rows.extend(evaluated)

    arm_summary = _arm_summary(per_run)
    selected_means = {
        arm: float(arm_summary[arm]["mean_selected_terminal_zero_sensitivity"])
        for arm in PILOT_ARMS
    }
    oracle_means = {
        arm: float(
            arm_summary[arm]["mean_oracle_at_20_terminal_zero_sensitivity"]
        )
        for arm in PILOT_ARMS
    }
    selected_best = max(selected_means[arm] for arm in PILOT_COMPARATORS)
    oracle_best = max(oracle_means[arm] for arm in PILOT_COMPARATORS)
    selected_best_arms = [
        arm
        for arm in PILOT_COMPARATORS
        if math.isclose(selected_means[arm], selected_best, abs_tol=1e-12)
    ]
    oracle_best_arms = [
        arm
        for arm in PILOT_COMPARATORS
        if math.isclose(oracle_means[arm], oracle_best, abs_tol=1e-12)
    ]
    e_vs_c = _paired_comparison(per_run, "C")
    e_vs_mtx = _paired_comparison(per_run, "MTX")
    dsl = _dsl_summary(per_run, candidate_rows)
    selected_crosschecks = sum(run["selected"]["exists"] for run in per_run)

    diagnostic: dict[str, Any] = {
        "schema_version": ORACLE_DIAGNOSTIC_SCHEMA_VERSION,
        "kind": "post-outcome-oracle-diagnostic",
        "inference": {
            "scope": "post_hoc_development_diagnostic",
            "primary_result_changed": False,
            "classification_issued": False,
            "primary_classification_not_recomputed": True,
            "primary_classification_source": (
                "separately versioned staged-pilot analysis artifact"
            ),
            "candidate_private_test_accessed": True,
            "candidate_sets_iid": False,
            "candidate_sets_description": (
                "probe-adaptive trajectories of 20 committed logical slots"
            ),
            "eight_worlds_are_now_calibration_data": True,
        },
        "source": {
            "s3_snapshot_sha256": sha256_bytes(snapshot_bytes),
            "campaign_manifest_file_sha256": sha256_bytes(manifest_bytes),
            "campaign_manifest_payload_sha256": manifest["payload_sha256"],
            "scientific_config_sha256": manifest_payload[
                "scientific_config_sha256"
            ],
            "checkpoint_set_sha256": sha256_json(checkpoint_binding),
            "world_seal_set_sha256": sha256_json(seal_binding),
            "replay_critical_source_sha256": critical_hashes,
            "diagnostic_implementation_sha256": hashlib.sha256(
                Path(__file__).read_bytes()
            ).hexdigest(),
            "diagnostic_spec_sha256": sha256_bytes(
                (Path(__file__).resolve().parents[1] / "oracle-diagnostic-spec.md")
                .read_bytes()
            ),
        },
        "integrity": {
            "checkpoints_verified": RUN_COUNT,
            "world_seals_verified": WORLD_COUNT,
            "runs_replayed_before_private_test": RUN_COUNT,
            "selected_test_crosschecks": selected_crosschecks,
            "terminal_no_selection_runs": RUN_COUNT - selected_crosschecks,
            "search_eligible_candidates_evaluated": len(candidate_rows),
            "snapshot_runs_exactly_reproduced": RUN_COUNT,
            "original_snapshot_unchanged": True,
            "issues": [],
        },
        "analysis_rules": {
            "oracle_pool": "20 committed logical slots per world-by-arm run",
            "search_eligibility": "syntax_valid && runtime_valid",
            "test_points_per_candidate": TEST_POINTS_PER_RUN,
            "all_invalid_observed_score": None,
            "terminal_zero_is_post_hoc_sensitivity": True,
            "unit_of_aggregation": "equal-weighted world",
            "oracle_is_deployable_selector": False,
            "oracle_warning": (
                "Oracle@20 selects and scores on the private test and is an "
                "optimistic candidate-pool ceiling"
            ),
        },
        "per_run": per_run,
        "arm_summary": arm_summary,
        "paired_diagnostics": {
            "E_vs_C": e_vs_c,
            "E_vs_MTX": e_vs_mtx,
        },
        "adaptive_vs_best_post_hoc_comparator_by_metric": {
            "selected_terminal_zero_sensitivity": {
                "post_hoc_best_comparator_arms": selected_best_arms,
                "post_hoc_best_comparator_mean": selected_best,
                "E_mean": selected_means["E"],
                "E_minus_best": selected_means["E"] - selected_best,
            },
            "oracle_at_20_terminal_zero_sensitivity": {
                "post_hoc_best_comparator_arms": oracle_best_arms,
                "post_hoc_best_comparator_mean": oracle_best,
                "E_mean": oracle_means["E"],
                "E_minus_best": oracle_means["E"] - oracle_best,
            },
        },
        "diagnostic_readout": {
            "E_oracle_at_20_exceeds_C": oracle_means["E"] > oracle_means["C"],
            "E_selection_regret_exceeds_C": (
                float(
                    arm_summary["E"][
                        "mean_selection_regret_terminal_zero_sensitivity"
                    ]
                )
                > float(
                    arm_summary["C"][
                        "mean_selection_regret_terminal_zero_sensitivity"
                    ]
                )
            ),
            "selector_only_explanation_supported": (
                oracle_means["E"] > oracle_means["C"]
                and selected_means["E"] <= selected_means["C"]
            ),
            "interpretation": (
                "descriptive only; candidate-pool quality, DSL validity, and "
                "selector behavior are not randomized mechanisms"
            ),
        },
        "dsl_validity": dsl,
        "caveats": [
            "The diagnostic was designed after the primary outcome and "
            "preliminary aggregate sanity recomputations were known.",
            "Oracle@20 is optimistically selected on the same private test used to score it.",
            "Candidates within an episode are probe-adaptive, clustered, and not IID.",
            "No p-values, confidence intervals, or population-generalization claims are issued.",
            "Candidate expressions, candidate-level test scores, test labels, "
            "and predictions are omitted.",
        ],
    }
    try:
        encoded = json.dumps(
            diagnostic,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise OracleDiagnosticError("diagnostic result is not finite JSON") from exc
    for forbidden in (
        "candidate_expression",
        "test_examples",
        "test_labels",
        "raw_prompt",
        "raw_response",
        "authorization",
        "api_key",
        "endpoint",
        "base_url",
    ):
        if forbidden in encoded.lower():
            raise OracleDiagnosticError(
                f"diagnostic output contains forbidden public field: {forbidden}"
            )
    return diagnostic


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(f"refusing to overwrite immutable file: {args.output}")
    result = analyze_oracle_diagnostic(args.campaign_dir, args.snapshot)
    atomic_publish_public_snapshot(args.output, result)
    print(
        json.dumps(
            {
                "status": "complete",
                "output": str(args.output),
                "E_oracle": result["arm_summary"]["E"][
                    "mean_oracle_at_20_terminal_zero_sensitivity"
                ],
                "C_oracle": result["arm_summary"]["C"][
                    "mean_oracle_at_20_terminal_zero_sensitivity"
                ],
                "selector_only_explanation_supported": result[
                    "diagnostic_readout"
                ]["selector_only_explanation_supported"],
            },
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


__all__ = [
    "ORACLE_DIAGNOSTIC_SCHEMA_VERSION",
    "OracleDiagnosticError",
    "REPLAY_CRITICAL_FILES",
    "_boolean_rate",
    "_kendall_tau_b",
    "_pearson",
    "_spearman",
    "analyze_oracle_diagnostic",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
