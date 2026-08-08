"""Compact, non-inferential analysis for the one-world live execution gate.

The experiment harness emits a detailed JSON summary.  This module reduces a
completed single-world summary to the diagnostics needed for the operational
go/no-go review.  A single world is never treated as a statistical sample.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import json
import math
from pathlib import Path
from typing import Any

from .runner import CANDIDATE_FORMATS


ANALYSIS_SCHEMA_VERSION = 2
REQUIRED_CONTRAST_ARMS = frozenset({"L", "H", "M", "MTX", "E"})
LEGACY_SCHEMA_FAILURE_SENTINEL = "__INVALID_JSON_CANDIDATE_SCHEMA__"
OVERALL_SCHEMA_ADHERENCE_THRESHOLD = 0.90
PER_ARM_SCHEMA_ADHERENCE_THRESHOLD = 0.80
OPTIONAL_CANDIDATE_TOKEN_FIELDS = (
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "reasoning_tokens",
)
ONE_WORLD_CAVEAT = (
    "This is operational, non-inferential triage of one serially executed world. "
    "It cannot establish generalization or statistical significance; arm execution "
    "and cache order may confound token and latency comparisons."
)


class GateAnalysisError(ValueError):
    """Raised when an experiment summary cannot support the gate analysis."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GateAnalysisError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise GateAnalysisError(f"{field} must be an array")
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise GateAnalysisError(f"{field} must be a boolean")
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GateAnalysisError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise GateAnalysisError(f"{field} must be finite")
    if minimum is not None and result < minimum:
        raise GateAnalysisError(f"{field} must be >= {minimum}")
    if maximum is not None and result > maximum:
        raise GateAnalysisError(f"{field} must be <= {maximum}")
    return result


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise GateAnalysisError(f"{field} must be an integer >= {minimum}")
    return value


def _optional_number(
    value: Any,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if value is None:
        return None
    return _number(value, field, minimum=minimum, maximum=maximum)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _candidate_format(candidate: Mapping[str, Any]) -> tuple[str, bool, bool]:
    """Return format label, schema adherence, and legacy-derivation status."""

    if "candidate_format" in candidate:
        value = candidate["candidate_format"]
        if not isinstance(value, str) or value not in CANDIDATE_FORMATS:
            return "invalid_candidate_format_metadata", False, False
        return value, value == "json_expression", False

    # Gate B predates candidate_format.  Its provider adapter replaced every
    # malformed JSON envelope with one fixed, secret-free sentinel.  A
    # non-sentinel serialized expression therefore denotes the legacy success
    # path; missing serialized content remains unknown and fails closed.
    expression = candidate.get("candidate_expression")
    if expression == LEGACY_SCHEMA_FAILURE_SENTINEL:
        return "legacy_schema_failure", False, True
    if isinstance(expression, str) and expression:
        return "json_expression", True, True
    return "legacy_candidate_format_unknown", False, True


def _candidate_format_summary(
    candidates: Sequence[Mapping[str, Any]],
    *,
    planned_calls: int,
    threshold: float,
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    adherent = 0
    derived = 0
    for candidate in candidates:
        label, is_adherent, is_derived = _candidate_format(candidate)
        counts[label] += 1
        adherent += int(is_adherent)
        derived += int(is_derived)
    missing_planned = max(planned_calls - len(candidates), 0)
    if missing_planned:
        counts["missing_planned_response"] += missing_planned
    denominator = planned_calls
    rate = _rate(adherent, denominator)
    if derived == len(candidates) and candidates:
        source = "legacy-derived"
    elif derived:
        source = "mixed"
    else:
        source = "reported"
    stable_counts = dict(sorted(counts.items()))
    return {
        "candidate_format_distribution": stable_counts,
        "candidate_format_rate_per_planned_call": {
            label: _rate(count, denominator) for label, count in stable_counts.items()
        },
        "candidate_format_source": source,
        "candidate_format_derived": derived > 0,
        "candidate_format_derived_count": derived,
        "schema_adherent_count": adherent,
        "schema_adherence_rate": rate,
        "schema_adherence_threshold": threshold,
        "schema_adherence_passed": rate >= threshold,
    }


def _candidate_format_by_round(
    candidates: Sequence[Mapping[str, Any]],
    *,
    threshold: float,
) -> dict[str, dict[str, Any]]:
    """Report format adherence by observed round without driving classification.

    A legacy artifact may not contain ``round_index``.  Such records remain
    auditable under one explicit ``unknown`` bucket instead of being silently
    dropped.  Completed live gates have equal, preplanned slots per round, so
    the observed bucket size is also the planned denominator there.
    """

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        value = candidate.get("round_index")
        label = str(value) if type(value) is int and value >= 0 else "unknown"
        grouped.setdefault(label, []).append(candidate)
    return {
        label: _candidate_format_summary(
            records,
            planned_calls=len(records),
            threshold=threshold,
        )
        for label, records in sorted(
            grouped.items(),
            key=lambda item: (
                item[0] == "unknown",
                int(item[0]) if item[0].isdigit() else 0,
            ),
        )
    }


def _optional_candidate_usage(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for field in OPTIONAL_CANDIDATE_TOKEN_FIELDS:
        values: list[int] = []
        for index, candidate in enumerate(candidates):
            value = candidate.get(field)
            if value is None:
                continue
            values.append(_integer(value, f"candidates[{index}].{field}"))
        total = sum(values) if values else None
        result[field] = {
            "reported_calls": len(values),
            "complete": len(values) == len(candidates),
            "total": total,
            "tokens_per_reported_call": (
                total / len(values) if total is not None else None
            ),
        }
    return result


def _usage_summary(
    budget: Mapping[str, Any],
    *,
    candidate_count: int,
    field: str,
) -> dict[str, Any]:
    planned = _integer(
        budget.get("generation_calls_planned", candidate_count),
        f"{field}.generation_calls_planned",
    )
    completed = _integer(
        budget.get("generation_calls_completed", candidate_count),
        f"{field}.generation_calls_completed",
    )
    available = _boolean(
        budget.get("actual_usage_available", False),
        f"{field}.actual_usage_available",
    )

    totals: dict[str, int | float | None] = {
        "actual_input_tokens": None,
        "actual_output_tokens": None,
        "actual_billed_tokens": None,
        "provider_requests": None,
        "retry_count": None,
        "latency_ms_total": None,
    }
    if available:
        for name in (
            "actual_input_tokens",
            "actual_output_tokens",
            "actual_billed_tokens",
            "provider_requests",
            "retry_count",
        ):
            totals[name] = _integer(budget.get(name), f"{field}.{name}")
        totals["latency_ms_total"] = _number(
            budget.get("latency_ms_total"),
            f"{field}.latency_ms_total",
            minimum=0.0,
        )

    denominator = completed if available and completed else 0
    return {
        "generation_calls_planned": planned,
        "generation_calls_completed": completed,
        "run_complete": completed == planned,
        "candidate_ledger_consistent": completed == candidate_count,
        "actual_usage_available": available,
        **totals,
        "input_tokens_per_call": (
            totals["actual_input_tokens"] / denominator if denominator else None
        ),
        "output_tokens_per_call": (
            totals["actual_output_tokens"] / denominator if denominator else None
        ),
        "billed_tokens_per_call": (
            totals["actual_billed_tokens"] / denominator if denominator else None
        ),
        "latency_ms_per_call": (
            totals["latency_ms_total"] / denominator if denominator else None
        ),
    }


def _arm_summary(
    run: Mapping[str, Any], arm_id: str
) -> tuple[dict[str, Any], list[Mapping[str, Any]]]:
    raw_candidates = _sequence(run.get("candidates"), f"runs[{arm_id}].candidates")
    candidates = [
        _mapping(candidate, f"runs[{arm_id}].candidates[{index}]")
        for index, candidate in enumerate(raw_candidates)
    ]
    valid: list[Mapping[str, Any]] = []
    for index, candidate in enumerate(candidates):
        syntax_valid = _boolean(
            candidate.get("syntax_valid"),
            f"runs[{arm_id}].candidates[{index}].syntax_valid",
        )
        runtime_valid = _boolean(
            candidate.get("runtime_valid"),
            f"runs[{arm_id}].candidates[{index}].runtime_valid",
        )
        if syntax_valid and runtime_valid:
            valid.append(candidate)

    canonical: set[str] = set()
    behavior: set[str] = set()
    for index, candidate in enumerate(valid):
        for name, destination in (
            ("canonical_hash", canonical),
            ("behavior_hash", behavior),
        ):
            value = candidate.get(name)
            if not isinstance(value, str) or not value:
                raise GateAnalysisError(
                    f"runs[{arm_id}] valid candidate {index} has no {name}"
                )
            destination.add(value)

    probe = _mapping(run.get("probe"), f"runs[{arm_id}].probe")
    final_test = _mapping(run.get("final_test"), f"runs[{arm_id}].final_test")
    probe_accuracy = _optional_number(
        probe.get("final_selected_accuracy"),
        f"runs[{arm_id}].probe.final_selected_accuracy",
        minimum=0.0,
        maximum=1.0,
    )
    test_accuracy = _optional_number(
        final_test.get("accuracy"),
        f"runs[{arm_id}].final_test.accuracy",
        minimum=0.0,
        maximum=1.0,
    )
    solved = _boolean(
        final_test.get("world_solved", False),
        f"runs[{arm_id}].final_test.world_solved",
    )
    budget = _mapping(run.get("budget"), f"runs[{arm_id}].budget")
    usage = _usage_summary(
        budget,
        candidate_count=len(candidates),
        field=f"runs[{arm_id}].budget",
    )
    format_summary = _candidate_format_summary(
        candidates,
        planned_calls=usage["generation_calls_planned"],
        threshold=PER_ARM_SCHEMA_ADHERENCE_THRESHOLD,
    )
    valid_count = len(valid)
    planned_calls = usage["generation_calls_planned"]
    return (
        {
            "candidate_count": len(candidates),
            "valid_count": valid_count,
            "valid_rate": _rate(valid_count, len(candidates)),
            "unique_canonical_count": len(canonical),
            "unique_canonical_rate": _rate(len(canonical), valid_count),
            "unique_canonical_per_planned_call": _rate(
                len(canonical), planned_calls
            ),
            "unique_behavior_count": len(behavior),
            "unique_behavior_rate": _rate(len(behavior), valid_count),
            "unique_behavior_per_planned_call": _rate(len(behavior), planned_calls),
            **format_summary,
            "candidate_format_by_round": _candidate_format_by_round(
                candidates,
                threshold=PER_ARM_SCHEMA_ADHERENCE_THRESHOLD,
            ),
            "final_probe_accuracy": probe_accuracy,
            "final_test_accuracy": test_accuracy,
            "world_solved": solved,
            **usage,
            "optional_candidate_usage": _optional_candidate_usage(candidates),
        },
        candidates,
    )


def _delta(left: float | None, right: float | None) -> float | None:
    return None if left is None or right is None else left - right


def _performance_delta(
    adaptive: Mapping[str, Any], comparator: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "final_probe_accuracy_delta": _delta(
            adaptive["final_probe_accuracy"], comparator["final_probe_accuracy"]
        ),
        "final_test_accuracy_delta": _delta(
            adaptive["final_test_accuracy"], comparator["final_test_accuracy"]
        ),
        "world_solved_delta": int(adaptive["world_solved"])
        - int(comparator["world_solved"]),
    }


def _fairness_summary(budget: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(budget.get("token_fairness", {}), "budget.token_fairness")
    available = _boolean(raw.get("available", False), "budget.token_fairness.available")
    passed = _boolean(raw.get("passed", False), "budget.token_fairness.passed")
    means_raw = _mapping(
        raw.get("mean_billed_tokens_per_call_by_arm", {}),
        "budget.token_fairness.mean_billed_tokens_per_call_by_arm",
    )
    means = {
        str(arm): _number(value, f"budget.token_fairness.mean[{arm}]", minimum=0.0)
        for arm, value in means_raw.items()
    }
    return {
        "available": available,
        "passed": passed,
        "threshold": _optional_number(
            raw.get("threshold"), "budget.token_fairness.threshold", minimum=0.0
        ),
        "relative_range": _optional_number(
            raw.get("relative_range"),
            "budget.token_fairness.relative_range",
            minimum=0.0,
        ),
        "mean_billed_tokens_per_call_by_arm": means,
    }


def _operational_concerns(
    summary: Mapping[str, Any],
    arms: Mapping[str, Mapping[str, Any]],
    global_format: Mapping[str, Any],
) -> list[str]:
    concerns: list[str] = []
    if summary.get("evidence") is True:
        concerns.append("one-world operational gate was incorrectly marked as evidence")
    if summary.get("mode") == "offline-smoke":
        concerns.append("source is an offline smoke run, not a live gate")
    if any(not arm["run_complete"] for arm in arms.values()):
        concerns.append("one or more arms did not complete their planned calls")
    if any(not arm["candidate_ledger_consistent"] for arm in arms.values()):
        concerns.append("candidate counts do not match completed-call ledgers")
    if any(
        arm["final_probe_accuracy"] is None or arm["final_test_accuracy"] is None
        for arm in arms.values()
    ):
        concerns.append("one or more final probe/test evaluations are missing")
    if any(not arm["actual_usage_available"] for arm in arms.values()):
        concerns.append("actual usage is unavailable for one or more arms")
    if not global_format["schema_adherence_passed"]:
        concerns.append("overall candidate-schema adherence is below the frozen threshold")
    failed_format_arms = sorted(
        arm_id
        for arm_id, arm in arms.items()
        if not arm["schema_adherence_passed"]
    )
    if failed_format_arms:
        concerns.append(
            "per-arm candidate-schema adherence is below the frozen threshold: "
            + ", ".join(failed_format_arms)
        )
    reported_reasoning = [
        arm["optional_candidate_usage"]["reasoning_tokens"]["total"]
        for arm in arms.values()
        if arm["optional_candidate_usage"]["reasoning_tokens"]["total"] is not None
    ]
    if any(value > 0 for value in reported_reasoning):
        concerns.append("provider reported reasoning tokens with thinking disabled")
    return concerns


def _classification(
    summary: Mapping[str, Any],
    arms: Mapping[str, Mapping[str, Any]],
    manipulation: Mapping[str, float],
    fairness: Mapping[str, Any],
    global_format: Mapping[str, Any],
) -> tuple[str, list[str]]:
    concerns = _operational_concerns(summary, arms, global_format)
    if manipulation["unique_canonical_per_planned_call_delta"] <= 0:
        concerns.append("H did not exceed L in canonical uniqueness per planned call")
    if manipulation["unique_behavior_per_planned_call_delta"] <= 0:
        concerns.append("H did not exceed L in behavioral uniqueness per planned call")
    if concerns:
        return "concerning", concerns

    e_test = arms["E"]["final_test_accuracy"]
    m_test = arms["M"]["final_test_accuracy"]
    mtx_test = arms["MTX"]["final_test_accuracy"]
    if e_test > m_test and e_test > mtx_test:
        resource_note = (
            "realized-token equivalence passed"
            if fairness["available"] and fairness["passed"]
            else "realized-token sensitivity is required"
        )
        return (
            "promising",
            [
                "opportunity accounting and candidate-schema checks passed, H exceeded L "
                "on both per-call diversity yields, E exceeded M and MTX on final-test "
                f"accuracy, and {resource_note}"
            ],
        )
    resource_note = (
        "realized-token equivalence passed"
        if fairness["available"] and fairness["passed"]
        else "realized-token sensitivity is required"
    )
    return (
        "neutral",
        [
            "opportunity accounting, candidate-schema, and manipulation checks passed, "
            "but E did not strictly exceed both M and MTX on this world's final-test "
            f"accuracy; {resource_note}"
        ],
    )


def analyze_gate(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Analyze one experiment-summary object without making inferential claims."""

    source = _mapping(summary, "summary")
    worlds = _sequence(source.get("worlds"), "summary.worlds")
    if len(worlds) != 1:
        raise GateAnalysisError(
            f"one-world gate analysis requires exactly one world; received {len(worlds)}"
        )
    world = _mapping(worlds[0], "summary.worlds[0]")
    raw_runs = _sequence(source.get("runs"), "summary.runs")
    if not raw_runs:
        raise GateAnalysisError("summary.runs cannot be empty")

    runs: dict[str, Mapping[str, Any]] = {}
    for index, raw_run in enumerate(raw_runs):
        run = _mapping(raw_run, f"summary.runs[{index}]")
        arm_id = run.get("arm_id")
        if not isinstance(arm_id, str) or not arm_id:
            raise GateAnalysisError(f"summary.runs[{index}].arm_id must be non-empty")
        if arm_id in runs:
            raise GateAnalysisError(f"duplicate run for arm {arm_id!r} in one-world summary")
        runs[arm_id] = run
    missing = sorted(REQUIRED_CONTRAST_ARMS - runs.keys())
    if missing:
        raise GateAnalysisError("required contrast arms are missing: " + ", ".join(missing))

    arms: dict[str, dict[str, Any]] = {}
    all_candidates: list[Mapping[str, Any]] = []
    for arm_id in sorted(runs):
        arm, candidates = _arm_summary(runs[arm_id], arm_id)
        arms[arm_id] = arm
        all_candidates.extend(candidates)

    h = arms["H"]
    low = arms["L"]
    manipulation = {
        "valid_rate_delta": h["valid_rate"] - low["valid_rate"],
        "unique_canonical_count_delta": h["unique_canonical_count"]
        - low["unique_canonical_count"],
        "unique_canonical_rate_delta": h["unique_canonical_rate"]
        - low["unique_canonical_rate"],
        "unique_behavior_count_delta": h["unique_behavior_count"]
        - low["unique_behavior_count"],
        "unique_behavior_rate_delta": h["unique_behavior_rate"]
        - low["unique_behavior_rate"],
        "unique_canonical_per_planned_call_delta": h[
            "unique_canonical_per_planned_call"
        ]
        - low["unique_canonical_per_planned_call"],
        "unique_behavior_per_planned_call_delta": h[
            "unique_behavior_per_planned_call"
        ]
        - low["unique_behavior_per_planned_call"],
    }
    contrasts = {
        "H_vs_L": manipulation,
        "E_vs_M": _performance_delta(arms["E"], arms["M"]),
        "E_vs_MTX": _performance_delta(arms["E"], arms["MTX"]),
    }

    top_budget = _mapping(source.get("budget"), "summary.budget")
    global_usage = _usage_summary(
        top_budget,
        candidate_count=len(all_candidates),
        field="summary.budget",
    )
    fairness = _fairness_summary(top_budget)
    global_format = _candidate_format_summary(
        all_candidates,
        planned_calls=global_usage["generation_calls_planned"],
        threshold=OVERALL_SCHEMA_ADHERENCE_THRESHOLD,
    )
    total_valid = sum(arm["valid_count"] for arm in arms.values())
    global_summary = {
        "world_count": 1,
        "arm_count": len(arms),
        "candidate_count": len(all_candidates),
        "valid_count": total_valid,
        "valid_rate": _rate(total_valid, len(all_candidates)),
        **global_format,
        "candidate_format_by_round": _candidate_format_by_round(
            all_candidates,
            threshold=OVERALL_SCHEMA_ADHERENCE_THRESHOLD,
        ),
        **global_usage,
        "all_runs_complete": all(arm["run_complete"] for arm in arms.values()),
        "all_candidate_ledgers_consistent": all(
            arm["candidate_ledger_consistent"] for arm in arms.values()
        ),
        "token_fairness": fairness,
        "realized_token_equivalence_passed": bool(
            fairness["available"] and fairness["passed"]
        ),
        "resource_sensitivity_required": not bool(
            fairness["available"] and fairness["passed"]
        ),
        "optional_candidate_usage": _optional_candidate_usage(all_candidates),
    }
    classification, reasons = _classification(
        source, arms, manipulation, fairness, global_format
    )
    readiness_issues = _operational_concerns(source, arms, global_format)
    result: dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "kind": "one-world-live-gate-analysis",
        "classification": classification,
        "classification_scope": "operational-only",
        "classification_reasons": reasons,
        "caveat": ONE_WORLD_CAVEAT,
        "source": {
            "experiment": source.get("experiment"),
            "config_hash": source.get("config_hash"),
            "evidence": source.get("evidence") is True,
            "evidence_scope": source.get("evidence_scope"),
            "mode": source.get("mode"),
            "world_hash": world.get("world_hash"),
        },
        "rate_denominators": {
            "valid_rate": "all candidates",
            "unique_canonical_rate": "valid candidates",
            "unique_behavior_rate": "valid candidates",
            "unique_canonical_per_planned_call": "planned generation calls",
            "unique_behavior_per_planned_call": "planned generation calls",
            "schema_adherence_rate": "planned generation calls",
        },
        "arms": arms,
        "contrasts": contrasts,
        "development_pilot_readiness": {
            "ready": not readiness_issues,
            "issues": readiness_issues,
            "one_world_H_vs_L_is_diagnostic_only": True,
            "rule": (
                "Gate C advances on complete engineering/accounting and frozen schema "
                "thresholds; the manipulation stop is evaluated across all eight "
                "development worlds."
            ),
        },
        "global": global_summary,
    }
    try:
        json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GateAnalysisError(f"analysis is not JSON safe: {exc}") from exc
    return result


def load_summary(path: str | Path) -> Mapping[str, Any]:
    """Load an experiment summary from UTF-8 JSON."""

    with Path(path).open("r", encoding="utf-8") as handle:
        return _mapping(json.load(handle), "summary")


def _write_new_json(path: str | Path, result: Mapping[str, Any]) -> None:
    """Write a JSON artifact once, refusing to replace an existing path."""

    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(encoded)


def write_new_json(path: str | Path, result: Mapping[str, Any]) -> None:
    """Public wrapper for exclusive JSON artifact creation."""

    _write_new_json(path, result)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="experiment summary JSON")
    parser.add_argument("--output", type=Path, help="new analysis JSON (never overwritten)")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    result = analyze_gate(load_summary(args.input))
    if args.output is not None:
        _write_new_json(args.output, result)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            allow_nan=False,
        )
    )
    return 0


__all__ = [
    "ANALYSIS_SCHEMA_VERSION",
    "GateAnalysisError",
    "ONE_WORLD_CAVEAT",
    "analyze_gate",
    "load_summary",
    "main",
    "write_new_json",
]


if __name__ == "__main__":
    raise SystemExit(main())
