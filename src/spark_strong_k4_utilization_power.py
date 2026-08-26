"""Offline prospective power calculations for the utilization challenge.

The module deliberately stops at operating characteristics.  It does not
import a model/provider adapter, materialise a private world, or mint a
benchmark.  All inferential quantities are computed with :class:`Fraction`.
The decimal ``value`` member attached to a fraction is presentation only and
is never used by a gate.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import subprocess
from typing import Any

from .provenance import PROJECT_ROOT, source_manifest


SCHEMA_VERSION = 1
PROTOCOL_ID = "spark-strong-k4-utilization-power-v1"
CONFIG_KIND = "spark-strong-k4-utilization-power-config"
PLAN_KIND = "spark-strong-k4-utilization-power-plan"
RESULT_KIND = "spark-strong-k4-utilization-power-result"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / f"{PROTOCOL_ID}.json"
DEFAULT_UPSTREAM_MANIFEST_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-strong-k4-utilization-feasibility-v2-20260825"
    / "artifact-manifest.json"
)
DEFAULT_POWER_ARTIFACT_DIRECTORY = (
    PROJECT_ROOT / "artifacts" / f"{PROTOCOL_ID}-20260826"
)
DEFAULT_POWER_PLAN_PATH = DEFAULT_POWER_ARTIFACT_DIRECTORY / "plan.json"
DEFAULT_POWER_RESULT_PATH = DEFAULT_POWER_ARTIFACT_DIRECTORY / "result.json"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_SAFE_MANIFEST_BYTES = 1024 * 1024
MAX_POWER_PLAN_BYTES = 5 * 1024 * 1024

FAMILY_ALPHA = Fraction(1, 20)
CONSERVATIVE_ALPHA = Fraction(1, 60)
FROZEN_P_FAVORABLE = Fraction(3, 5)
FROZEN_P_ADVERSE = Fraction(1, 10)
TARGET_POWER = Fraction(9, 10)
DESIGN_IDS = (
    "strict-fallback-q4",
    "strict-maximum-q6",
    "degraded-target-q8",
)
TIER_IDS = (
    "strict_unique_nonconstant_switch",
    "degraded_two_choice_disjoint_switch",
)

EVIDENCE_SCOPE = (
    "offline_prospective_power_design_for_outcome_conditioned_development_challenge_only"
)


class UtilizationPowerError(ValueError):
    """Raised when an offline utilization-power input fails closed."""


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise UtilizationPowerError("value is not canonical JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_sha256(value: object, label: str) -> str:
    if not _is_sha256(value):
        raise UtilizationPowerError(f"{label} must be a lowercase SHA-256")
    return str(value)


def _require_fraction(value: object, label: str) -> Fraction:
    # bool is an int subclass, but accepting it here would make a malformed
    # JSON payload look like an exact probability.
    if not isinstance(value, Fraction):
        raise UtilizationPowerError(f"{label} must be an exact Fraction")
    return value


def fraction_payload(value: Fraction) -> dict[str, Any]:
    """Render an exact fraction with a display-only decimal."""

    value = _require_fraction(value, "fraction")
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "value": float(value),
    }


_fraction_payload = fraction_payload


def _fraction_from_payload(value: object, label: str) -> Fraction:
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, Mapping):
        allowed_keys = {"numerator", "denominator"}
        if "value" in value:
            allowed_keys.add("value")
        if set(value) != allowed_keys:
            raise UtilizationPowerError(f"{label} fraction payload is non-canonical")
        numerator = value.get("numerator")
        denominator = value.get("denominator")
        if (
            type(numerator) is not int
            or type(denominator) is not int
            or denominator <= 0
        ):
            raise UtilizationPowerError(f"{label} fraction payload is malformed")
        result = Fraction(numerator, denominator)
        if "value" in value:
            display = value["value"]
            if (
                isinstance(display, bool)
                or not isinstance(display, (int, float))
                or not math.isfinite(float(display))
                or float(display) != float(result)
            ):
                raise UtilizationPowerError(f"{label} display value is malformed")
    else:
        raise UtilizationPowerError(f"{label} fraction payload is malformed")
    return result


def _validate_probability(value: Fraction, label: str) -> Fraction:
    value = _require_fraction(value, label)
    if not 0 <= value <= 1:
        raise UtilizationPowerError(f"{label} must be in [0, 1]")
    return value


def _validate_n(n: object, label: str = "n") -> int:
    if type(n) is not int or n < 0:
        raise UtilizationPowerError(f"{label} must be a non-negative integer")
    return n


def _validate_count(k: object, n: int, label: str = "count") -> int:
    if type(k) is not int or k < 0 or k > n + 1:
        raise UtilizationPowerError(f"{label} must be in 0..n+1")
    return k


def _binomial_coefficient(n: int, k: int) -> int:
    # Avoid importing math.comb at module scope in order to keep the arithmetic
    # seam conspicuous and deterministic.
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    result = 1
    for index in range(1, k + 1):
        result = result * (n - k + index) // index
    return result


def binomial_upper_tail(
    n: int,
    favorable_count: int,
    p: Fraction = Fraction(1, 2),
) -> Fraction:
    """Return ``P[Binomial(n, p) >= favorable_count]`` exactly."""

    n = _validate_n(n)
    favorable_count = _validate_count(favorable_count, n)
    p = _validate_probability(p, "p")
    if favorable_count > n:
        return Fraction(0, 1)
    return sum(
        (
            Fraction(_binomial_coefficient(n, successes), 1)
            * p**successes
            * (1 - p) ** (n - successes)
            for successes in range(favorable_count, n + 1)
        ),
        Fraction(0, 1),
    )


exact_binomial_upper_tail = binomial_upper_tail
binomial_tail = binomial_upper_tail


def critical_favorable_count(
    n: int,
    alpha: Fraction = FAMILY_ALPHA,
) -> int:
    """Return the smallest favorable count with a null upper tail <= alpha.

    ``n + 1`` is returned when no attainable count reaches the requested
    alpha.  This is useful for the zero-non-tie case and makes the gate
    explicit instead of silently treating an impossible rejection as a pass.
    """

    n = _validate_n(n)
    alpha = _validate_probability(_require_fraction(alpha, "alpha"), "alpha")
    if not 0 < alpha < 1:
        raise UtilizationPowerError("alpha must be a Fraction strictly between zero and one")
    for count in range(n + 1):
        if binomial_upper_tail(n, count, Fraction(1, 2)) <= alpha:
            return count
    return n + 1


binomial_critical_favorable_count = critical_favorable_count
exact_sign_critical_count = critical_favorable_count


def exact_power(
    n: int,
    p_favorable: Fraction,
    p_adverse: Fraction,
    alpha: Fraction = CONSERVATIVE_ALPHA,
) -> Fraction:
    """Calculate exact sign-test power under a tie-producing alternative.

    Let ``M`` be the number of non-ties, ``M ~ Binomial(n, d)`` with
    ``d = p_favorable + p_adverse``.  Conditional on ``M = m``, the favorable
    count is ``Binomial(m, theta)`` where ``theta = p_favorable / d``.  The
    critical count is recomputed for every possible ``m``; this is the exact
    conditional-on-non-ties sign-test convention.
    """

    n = _validate_n(n)
    p_favorable = _validate_probability(p_favorable, "p_favorable")
    p_adverse = _validate_probability(p_adverse, "p_adverse")
    if p_favorable + p_adverse > 1:
        raise UtilizationPowerError("p_favorable + p_adverse must be at most one")
    alpha = _require_fraction(alpha, "alpha")
    if not 0 < alpha < 1:
        raise UtilizationPowerError("alpha must be a Fraction strictly between zero and one")
    d = p_favorable + p_adverse
    if d == 0:
        return Fraction(0, 1)
    theta = p_favorable / d
    power = Fraction(0, 1)
    for m in range(n + 1):
        m_mass = (
            Fraction(_binomial_coefficient(n, m), 1)
            * d**m
            * (1 - d) ** (n - m)
        )
        critical = critical_favorable_count(m, alpha)
        conditional_tail = binomial_upper_tail(m, critical, theta)
        power += m_mass * conditional_tail
    return power


exact_sign_test_power = exact_power
binomial_sign_test_power = exact_power


def minimum_world_count_for_power(
    p_favorable: Fraction,
    p_adverse: Fraction,
    alpha: Fraction,
    target_power: Fraction,
    *,
    minimum_world_count: int,
    maximum_world_count: int,
    required_multiple: int = 1,
) -> int:
    """Return the first admissible world count meeting an exact power target."""

    minimum_world_count = _validate_n(minimum_world_count, "minimum_world_count")
    maximum_world_count = _validate_n(maximum_world_count, "maximum_world_count")
    if minimum_world_count > maximum_world_count:
        raise UtilizationPowerError("minimum world count exceeds maximum world count")
    if type(required_multiple) is not int or required_multiple <= 0:
        raise UtilizationPowerError("required_multiple must be a positive integer")
    target_power = _validate_probability(
        _require_fraction(target_power, "target_power"), "target_power"
    )
    if target_power == 0:
        raise UtilizationPowerError("target_power must be positive")
    for world_count in range(minimum_world_count, maximum_world_count + 1):
        if world_count % required_multiple:
            continue
        if exact_power(world_count, p_favorable, p_adverse, alpha) >= target_power:
            return world_count
    raise UtilizationPowerError("power target is unattainable within the frozen search range")


def exact_sign_test(
    favorable_count: int,
    adverse_count: int,
    alpha: Fraction = FAMILY_ALPHA,
) -> dict[str, Any]:
    """Evaluate one observed one-sided exact sign test."""

    if type(favorable_count) is not int or favorable_count < 0:
        raise UtilizationPowerError("favorable_count must be a non-negative integer")
    if type(adverse_count) is not int or adverse_count < 0:
        raise UtilizationPowerError("adverse_count must be a non-negative integer")
    alpha = _require_fraction(alpha, "alpha")
    if not 0 < alpha < 1:
        raise UtilizationPowerError("alpha must be a Fraction strictly between zero and one")
    non_ties = favorable_count + adverse_count
    p_value = binomial_upper_tail(non_ties, favorable_count, Fraction(1, 2))
    critical = critical_favorable_count(non_ties, alpha)
    rejected = favorable_count >= critical and p_value <= alpha
    return {
        "favorable_count": favorable_count,
        "adverse_count": adverse_count,
        "non_tie_count": non_ties,
        "tie_count": 0,
        "critical_favorable_count": critical,
        "exact_one_sided_p_value": fraction_payload(p_value),
        "reject_at_alpha": rejected,
    }


def classify_world_scores(
    own_context_correct_set_score: int | Fraction,
    cross_context_correct_set_score: int | Fraction,
) -> dict[str, Any]:
    """Classify one world/pair by own-minus-cross correct-set score."""

    for value, label in (
        (own_context_correct_set_score, "own_context_correct_set_score"),
        (cross_context_correct_set_score, "cross_context_correct_set_score"),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, Fraction)):
            raise UtilizationPowerError(f"{label} must be numeric")
    difference = Fraction(own_context_correct_set_score) - Fraction(
        cross_context_correct_set_score
    )
    direction = "favorable" if difference > 0 else "adverse" if difference < 0 else "tie"
    return {
        "own_context_correct_set_score": own_context_correct_set_score,
        "cross_context_correct_set_score": cross_context_correct_set_score,
        "difference": fraction_payload(difference),
        "classification": direction,
    }


classify_world = classify_world_scores
classify_pair = classify_world_scores


def summarize_world_scores(
    own_context_scores: Sequence[int | Fraction],
    cross_context_scores: Sequence[int | Fraction],
    *,
    alpha: Fraction = FAMILY_ALPHA,
) -> dict[str, Any]:
    """Summarise independent world/pair scores with the primary sign test."""

    if not isinstance(own_context_scores, Sequence) or isinstance(
        own_context_scores, (str, bytes)
    ):
        raise UtilizationPowerError("own_context_scores must be a sequence")
    if not isinstance(cross_context_scores, Sequence) or isinstance(
        cross_context_scores, (str, bytes)
    ):
        raise UtilizationPowerError("cross_context_scores must be a sequence")
    if len(own_context_scores) != len(cross_context_scores):
        raise UtilizationPowerError("own and cross score vectors must have equal length")
    counts = {"favorable": 0, "adverse": 0, "tie": 0}
    for own, cross in zip(own_context_scores, cross_context_scores, strict=True):
        counts[classify_world_scores(own, cross)["classification"]] += 1
    test = exact_sign_test(counts["favorable"], counts["adverse"], alpha)
    test["tie_count"] = counts["tie"]
    return {
        "independent_unit": "unique world/pair",
        "world_count": len(own_context_scores),
        "favorable_count": counts["favorable"],
        "adverse_count": counts["adverse"],
        "tie_count": counts["tie"],
        "non_tie_count": counts["favorable"] + counts["adverse"],
        "primary_test": test,
    }


summarize_pairs = summarize_world_scores


def _core_config_sections() -> tuple[str, ...]:
    return (
        "schema_version",
        "kind",
        "protocol_id",
        "evidence_scope",
        "upstream_geometry",
        "candidate_designs",
        "primary_estimand",
        "route_family",
        "power_model",
        "secondary_endpoints",
        "go_no_go",
        "artifact_contract",
    )


def _expected_fraction(section: Mapping[str, Any], label: str, value: Fraction) -> None:
    observed = _fraction_from_payload(section, label)
    if observed != value:
        raise UtilizationPowerError(f"{label} drifted")


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise UtilizationPowerError(f"{label} must be an object")
    return value


def _validate_designs(config: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    designs = config.get("candidate_designs")
    if not isinstance(designs, list) or len(designs) != 3:
        raise UtilizationPowerError("candidate_designs must contain exactly three designs")
    if [row.get("design_id") for row in designs if isinstance(row, Mapping)] != list(
        DESIGN_IDS
    ):
        raise UtilizationPowerError("candidate design order drifted")
    expected = (
        (
            "strict_unique_nonconstant_switch",
            4,
            16,
            "selected_fallback_geometry",
            1,
            (
                "paired net evidence of context-responsive unique-action "
                "utilization in the selected finite-DSL challenge only; complete "
                "two-arm switching is secondary"
            ),
            False,
        ),
        (
            "strict_unique_nonconstant_switch",
            6,
            24,
            (
                "maximum_exact_balanced_capacity_not_selected_by_the_feasibility_"
                "fallback_rule"
            ),
            1,
            (
                "paired net evidence of context-responsive unique-action utilization "
                "in a separately sealed q6 finite-DSL challenge only; complete "
                "two-arm switching is secondary"
            ),
            True,
        ),
        (
            "degraded_two_choice_disjoint_switch",
            8,
            32,
            "selected_target_geometry",
            2,
            (
                "paired net evidence of context-responsive disjoint-two-choice-set "
                "utilization only; never unique-action switching; complete two-arm "
                "switching is secondary"
            ),
            False,
        ),
    )
    result: list[Mapping[str, Any]] = []
    for row, expected_row in zip(designs, expected, strict=True):
        if not isinstance(row, Mapping):
            raise UtilizationPowerError("candidate design row is malformed")
        tier, q, n, geometry, set_size, claim_limit, rematching = expected_row
        if (
            row.get("tier_id"),
            row.get("balanced_q"),
            row.get("world_count"),
            row.get("geometry_status"),
            row.get("correct_set_size_each_arm"),
            row.get("claim_limit"),
            row.get("later_sealed_matching_required"),
        ) != (tier, q, n, geometry, set_size, claim_limit, rematching):
            raise UtilizationPowerError("candidate design constants drifted")
        result.append(row)
    return tuple(result)


def _validate_upstream_geometry(config: Mapping[str, Any]) -> Mapping[str, Any]:
    upstream = _require_mapping(config.get("upstream_geometry"), "upstream_geometry")
    for key in (
        "artifact_manifest_relative_path",
        "artifact_manifest_file_sha256",
        "artifact_manifest_sha256",
        "upstream_plan_sha256",
        "upstream_scan_sha256",
    ):
        if key.endswith("sha256"):
            _require_sha256(upstream.get(key), f"upstream_geometry.{key}")
        elif not isinstance(upstream.get(key), str) or Path(str(upstream[key])).is_absolute():
            raise UtilizationPowerError(f"upstream_geometry.{key} is malformed")
    if (
        upstream.get("upstream_world_count") != 1024
        or upstream.get("upstream_status") != "complete_outcome_conditioned_development_scan"
        or upstream.get("private_pair_or_target_identity_read_by_power_stage") is not False
        or upstream.get("model_outputs_read") is not False
        or upstream.get("provider_calls_made") != 0
        or upstream.get("final_benchmark_minted") is not False
    ):
        raise UtilizationPowerError("upstream geometry safety boundary drifted")
    return upstream


def validate_config(config: Mapping[str, Any]) -> None:
    """Strictly validate the frozen prospective-power configuration."""

    if not isinstance(config, Mapping) or set(config) != set(_core_config_sections()):
        raise UtilizationPowerError("power config uses a non-canonical schema")
    if (
        config.get("schema_version") != SCHEMA_VERSION
        or config.get("kind") != CONFIG_KIND
        or config.get("protocol_id") != PROTOCOL_ID
        or config.get("evidence_scope") != EVIDENCE_SCOPE
    ):
        raise UtilizationPowerError("power config identity drifted")
    _validate_upstream_geometry(config)
    designs = _validate_designs(config)

    estimand = _require_mapping(config.get("primary_estimand"), "primary_estimand")
    expected_estimand = {
        "independent_unit": "unique development world and its two-context pair",
        "calls_are_not_independent_units": True,
        "routes_may_not_be_pooled_as_independent_worlds": True,
        "arm_score": (
            "one iff the chosen raw action belongs to that arm's frozen "
            "nonconstant-K4 correct set; otherwise zero"
        ),
        "cross_score": (
            "rescore each arm's chosen raw action against the other arm's "
            "frozen disjoint correct set"
        ),
        "world_signed_score": (
            "sum of the two own-context arm scores minus the sum of the two "
            "cross-context scores"
        ),
        "favorable_world": "world_signed_score > 0",
        "adverse_world": "world_signed_score < 0",
        "tie_world": "world_signed_score = 0",
        "primary_test": "one-sided exact sign test conditional on non-tie worlds",
        "null": (
            "under no context utilization, the joint two-arm observable outcomes, "
            "including received and validity status plus parsed choice when valid, "
            "are exchangeable under swapping the arm labels after conditioning on "
            "the frozen pair design and schedule; therefore, conditional on a "
            "non-tie, favorable probability is at most one half"
        ),
        "null_exchangeability_assumption": (
            "the two arm observable outcomes, including received and validity "
            "status plus parsed choice when valid, are jointly exchangeable under "
            "arm-label swap within each world under the null; independent "
            "identically distributed arm outcomes are sufficient but not necessary"
        ),
        "choice_bias_control": (
            "under the explicit joint-exchangeability assumption and frozen "
            "pair-shared action order, a fixed arm-invariant raw-action or "
            "display-position bias is sign-symmetric; equality of expected own "
            "and cross scores or hard balance alone does not establish the exact "
            "sign-test null"
        ),
        "required_execution_controls": [
            "pair-shared opaque option IDs and raw-action order",
            "stateless one-shot calls",
            (
                "arm-order and display schedules sealed independently of world "
                "content, target identities and model outcomes"
            ),
            (
                "same prompt wording and response contract apart from the "
                "rendered context"
            ),
            (
                "pre-live justification and canary of joint arm-exchangeability for "
                "received status, validity and parsed choice; if it is not "
                "defensible, the sign-test gate is not valid"
            ),
        ],
        "invalid_received_response": (
            "if either arm has a received but invalid response, classify the entire "
            "world as a primary tie and a complete-switch miss; keep it in the "
            "fixed denominator"
        ),
        "transport_or_missing_response": (
            "the affected complete route attempt is non-evaluable; no retry, "
            "replacement world, or partial denominator"
        ),
        "tier_mixing_allowed": False,
    }
    if any(estimand.get(key) != value for key, value in expected_estimand.items()):
        raise UtilizationPowerError("primary estimand contract drifted")

    route = _require_mapping(config.get("route_family"), "route_family")
    family_alpha = _require_mapping(route.get("family_alpha"), "route_family.family_alpha")
    conservative_alpha = _require_mapping(
        route.get("conservative_design_alpha"), "route_family.conservative_design_alpha"
    )
    _expected_fraction(family_alpha, "family_alpha", FAMILY_ALPHA)
    _expected_fraction(conservative_alpha, "conservative_design_alpha", CONSERVATIVE_ALPHA)
    if (
        route.get("planned_route_hypothesis_count") != 3
        or route.get("route_identities_frozen_by_this_power_stage") is not False
        or route.get("future_multiplicity_method")
        != "Holm step-down across the three frozen route hypotheses within one selected tier"
        or route.get("conservative_design_alpha_interpretation")
        != (
            "a route reaching this raw threshold survives the first Holm step "
            "regardless of the other two route p-values"
        )
        or route.get("joint_route_power_estimated") is not False
    ):
        raise UtilizationPowerError("route-family contract drifted")

    power_model = _require_mapping(config.get("power_model"), "power_model")
    frozen = _require_mapping(power_model.get("frozen_sesoi"), "power_model.frozen_sesoi")
    _expected_fraction(
        _require_mapping(frozen.get("p_favorable"), "frozen p_favorable"),
        "frozen p_favorable",
        FROZEN_P_FAVORABLE,
    )
    _expected_fraction(
        _require_mapping(frozen.get("p_adverse"), "frozen p_adverse"),
        "frozen p_adverse",
        FROZEN_P_ADVERSE,
    )
    _expected_fraction(
        _require_mapping(frozen.get("p_tie"), "frozen p_tie"),
        "frozen p_tie",
        Fraction(3, 10),
    )
    _expected_fraction(
        _require_mapping(
            frozen.get("conditional_favorable_probability"), "conditional theta"
        ),
        "conditional theta",
        Fraction(6, 7),
    )
    _expected_fraction(
        _require_mapping(
            frozen.get("net_favorable_minus_adverse"), "net direction"
        ),
        "net direction",
        Fraction(1, 2),
    )
    target = _require_mapping(power_model.get("target_power"), "power_model.target_power")
    _expected_fraction(target, "target_power", TARGET_POWER)
    search = _require_mapping(
        power_model.get("minimum_sample_search"), "power_model.minimum_sample_search"
    )
    if (
        search.get("minimum_world_count") != 1
        or search.get("maximum_world_count") != 128
        or search.get("balanced_stratum_count") != 4
        or power_model.get("gate_uses_conservative_design_alpha") is not True
        or power_model.get("gate_uses_only_frozen_sesoi") is not True
    ):
        raise UtilizationPowerError("power-model gate contract drifted")
    scenarios = power_model.get("sensitivity_scenarios")
    expected_scenarios = {
        "weak": (Fraction(2, 5), Fraction(1, 5)),
        "moderate": (Fraction(1, 2), Fraction(3, 20)),
        "frozen-sesoi": (FROZEN_P_FAVORABLE, FROZEN_P_ADVERSE),
        "strong": (Fraction(7, 10), Fraction(1, 20)),
        "near-ideal": (Fraction(4, 5), Fraction(0, 1)),
    }
    observed_scenario_ids = [
        row.get("scenario_id") for row in scenarios if isinstance(row, Mapping)
    ] if isinstance(scenarios, list) else []
    if not isinstance(scenarios, list) or observed_scenario_ids != list(expected_scenarios):
        raise UtilizationPowerError("power sensitivity scenarios drifted")
    for row in scenarios:
        if not isinstance(row, Mapping):
            raise UtilizationPowerError("sensitivity scenario is malformed")
        scenario_id = row["scenario_id"]
        pf = _fraction_from_payload(row.get("p_favorable"), f"scenario {scenario_id} p_favorable")
        pa = _fraction_from_payload(row.get("p_adverse"), f"scenario {scenario_id} p_adverse")
        if (pf, pa) != expected_scenarios[scenario_id]:
            raise UtilizationPowerError(f"scenario {scenario_id} constants drifted")

    secondary = _require_mapping(config.get("secondary_endpoints"), "secondary_endpoints")
    _expected_fraction(
        _require_mapping(
            secondary.get("strict_uniform_independent_calibration_probability"),
            "strict uniform calibration",
        ),
        "strict uniform calibration",
        Fraction(1, 100),
    )
    _expected_fraction(
        _require_mapping(
            secondary.get("degraded_uniform_independent_calibration_probability"),
            "degraded uniform calibration",
        ),
        "degraded uniform calibration",
        Fraction(1, 25),
    )
    if (
        secondary.get("uniform_calibration_is_primary_inference") is not False
        or (
            secondary.get(
                "route_specific_choice_baseline_power_identifiable_from_safe_manifest"
            )
            is not False
        )
    ):
        raise UtilizationPowerError("secondary endpoint boundary drifted")
    go = _require_mapping(config.get("go_no_go"), "go_no_go")
    if (
        go.get("per_design_pass_rule")
        != (
            "exact primary power at the frozen SESOI and conservative design "
            "alpha is at least target_power"
        )
        or go.get("strict_and_degraded_classified_separately") is not True
        or go.get("degraded_pass_may_relabel_strict_failure") is not False
        or go.get("passing_power_mints_final_benchmark") is not False
    ):
        raise UtilizationPowerError("go/no-go boundary drifted")
    artifact = _require_mapping(config.get("artifact_contract"), "artifact_contract")
    expected_artifact = {
        "evidence": False,
        "confirmatory": False,
        "development_only": True,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "private_geometry_read": False,
        "final_benchmark_minted": False,
        "exclusive_create_no_overwrite": True,
        "output_mode": "0600",
        "plan_requires_source_freeze_commit": True,
        "result_requires_operator_reviewed_plan_semantic_and_file_sha256": True,
    }
    if any(artifact.get(key) != value for key, value in expected_artifact.items()):
        raise UtilizationPowerError("artifact contract drifted")
    # Keep the local variable live for readers and static checkers: design
    # validation above is intentionally part of config validation.
    if len(designs) != 3:
        raise AssertionError("unreachable design count")


def _load_frozen_config(
    config: Mapping[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    expected_file_sha256: str | None = None,
) -> tuple[dict[str, Any], str]:
    loaded, observed_sha = _read_json_bounded(
        config_path,
        "power config",
        maximum_bytes=MAX_CONFIG_BYTES,
        expected_path=DEFAULT_CONFIG_PATH,
    )
    if expected_file_sha256 is not None and observed_sha != _require_sha256(
        expected_file_sha256, "config_file_sha256"
    ):
        raise UtilizationPowerError("supplied config file SHA-256 differs from bytes")
    if _canonical_json_bytes(loaded) != _canonical_json_bytes(config):
        raise UtilizationPowerError("config mapping differs from frozen config file bytes")
    validate_config(loaded)
    return loaded, observed_sha


def _current_source_state() -> tuple[str, str, list[str], str]:
    observed = source_manifest(PROJECT_ROOT)
    manifest_sha = _require_sha256(
        observed.get("source_manifest_sha256"), "current source manifest"
    )
    environment = _require_mapping(
        observed.get("environment"), "current source environment"
    )
    git_head = environment.get("git_head")
    if (
        not isinstance(git_head, str)
        or len(git_head) != 40
        or any(character not in "0123456789abcdef" for character in git_head)
    ):
        raise UtilizationPowerError("current source is not bound to a Git commit")
    files = observed.get("files")
    if not isinstance(files, list) or not files:
        raise UtilizationPowerError("current source manifest has no files")
    relative_paths: list[str] = []
    config_entry_sha: str | None = None
    config_relative = DEFAULT_CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix()
    for row in files:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise UtilizationPowerError("current source manifest file row is malformed")
        relative_path = str(row["path"])
        relative_paths.append(relative_path)
        if relative_path == config_relative:
            config_entry_sha = _require_sha256(
                row.get("sha256"), "current source config entry"
            )
    if config_entry_sha is None:
        raise UtilizationPowerError("current source manifest omits the power config")
    return manifest_sha, git_head, relative_paths, config_entry_sha


def _require_clean_source_freeze(relative_paths: Sequence[str]) -> None:
    try:
        completed = subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *relative_paths,
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise UtilizationPowerError("cannot verify the source-freeze commit") from exc
    if completed.stdout.strip():
        raise UtilizationPowerError(
            "protocol source is not clean relative to the source-freeze commit"
        )


def _require_frozen_commit_matches_source(
    frozen_git_head: str,
    relative_paths: Sequence[str],
) -> None:
    if (
        len(frozen_git_head) != 40
        or any(character not in "0123456789abcdef" for character in frozen_git_head)
    ):
        raise UtilizationPowerError("source-freeze Git commit is malformed")
    commands = (
        ["git", "merge-base", "--is-ancestor", frozen_git_head, "HEAD"],
        ["git", "diff", "--quiet", frozen_git_head, "--", *relative_paths],
    )
    for command in commands:
        try:
            subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UtilizationPowerError(
                "source-freeze commit does not match current protocol files"
            ) from exc


def _check_source_binding(
    expected: str,
    *,
    require_current_source: bool,
    config_file_sha256: str | None = None,
) -> tuple[str, list[str]]:
    _require_sha256(expected, "source_manifest_sha256")
    current_sha, git_head, relative_paths, source_config_sha = _current_source_state()
    if require_current_source:
        if current_sha != expected:
            raise UtilizationPowerError("source manifest drifted")
        if (
            config_file_sha256 is not None
            and source_config_sha
            != _require_sha256(config_file_sha256, "config_file_sha256")
        ):
            raise UtilizationPowerError("source manifest config binding drifted")
        _require_clean_source_freeze(relative_paths)
    return git_head, relative_paths


def _check_upstream_binding(
    config: Mapping[str, Any],
    *,
    artifact_manifest_path: str | Path = DEFAULT_UPSTREAM_MANIFEST_PATH,
) -> dict[str, str]:
    upstream = _validate_upstream_geometry(config)
    expected_file = str(upstream["artifact_manifest_file_sha256"])
    expected_inner = str(upstream["artifact_manifest_sha256"])
    expected_scan = str(upstream["upstream_scan_sha256"])
    artifact_manifest, observed_file = _read_json_bounded(
        artifact_manifest_path,
        "safe artifact manifest",
        maximum_bytes=MAX_SAFE_MANIFEST_BYTES,
        expected_path=DEFAULT_UPSTREAM_MANIFEST_PATH,
    )
    if observed_file != expected_file:
        raise UtilizationPowerError("upstream artifact-manifest bytes differ")
    unsigned = {
        key: value for key, value in artifact_manifest.items() if key != "manifest_sha256"
    }
    if (
        artifact_manifest.get("manifest_sha256") != expected_inner
        or _sha256_json(unsigned) != expected_inner
    ):
        raise UtilizationPowerError("upstream artifact manifest digest mismatch")
    # Only inspect safe metadata.  In particular, never read the path in
    # ``result_raw_private``; the scan digest is a binding, not an input.
    artifacts = _require_mapping(
        artifact_manifest.get("artifacts"), "artifact manifest artifacts"
    )
    plan_meta = _require_mapping(
        artifacts.get("plan"), "artifact manifest plan metadata"
    )
    result_meta = _require_mapping(
        artifacts.get("result_raw_private"), "artifact manifest result metadata"
    )
    bindings = _require_mapping(
        artifact_manifest.get("bindings"), "artifact manifest bindings"
    )
    if (
        plan_meta.get("plan_sha256") != upstream["upstream_plan_sha256"]
        or bindings.get("plan_sha256") != upstream["upstream_plan_sha256"]
        or result_meta.get("scan_sha256") != expected_scan
        or bindings.get("scan_sha256") != expected_scan
    ):
        raise UtilizationPowerError("upstream plan or scan binding mismatch")
    safety = _require_mapping(
        artifact_manifest.get("safety"), "artifact manifest safety"
    )
    if (
        safety.get("model_outputs_read") is not False
        or type(safety.get("provider_calls_made")) is not int
        or safety.get("provider_calls_made") != 0
        or safety.get("final_benchmark_minted") is not False
    ):
        raise UtilizationPowerError("upstream artifact safety boundary drifted")
    return {
        "artifact_manifest_file_sha256": expected_file,
        "artifact_manifest_sha256": expected_inner,
        "upstream_scan_sha256": expected_scan,
        "upstream_plan_sha256": str(upstream["upstream_plan_sha256"]),
    }


def _design_row(
    design: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    n = int(design["world_count"])
    scenario_rows: list[dict[str, Any]] = []
    frozen_power: Fraction | None = None
    for scenario in scenarios:
        sid = str(scenario["scenario_id"])
        pf = _fraction_from_payload(scenario["p_favorable"], f"scenario {sid} p_favorable")
        pa = _fraction_from_payload(scenario["p_adverse"], f"scenario {sid} p_adverse")
        power = exact_power(n, pf, pa, CONSERVATIVE_ALPHA)
        row = {
            "scenario_id": sid,
            "p_favorable": fraction_payload(pf),
            "p_adverse": fraction_payload(pa),
            "exact_power": fraction_payload(power),
        }
        scenario_rows.append(row)
        if sid == "frozen-sesoi":
            frozen_power = power
    assert frozen_power is not None
    return {
        "design_id": design["design_id"],
        "tier_id": design["tier_id"],
        "balanced_q": design["balanced_q"],
        "world_count": n,
        "geometry_status": design["geometry_status"],
        "correct_set_size_each_arm": design["correct_set_size_each_arm"],
        "claim_limit": design["claim_limit"],
        "later_sealed_matching_required": design["later_sealed_matching_required"],
        "conservative_design_alpha": fraction_payload(CONSERVATIVE_ALPHA),
        "frozen_sesoi_exact_power": fraction_payload(frozen_power),
        "target_power": fraction_payload(TARGET_POWER),
        "passes_target_power": frozen_power >= TARGET_POWER,
        "sensitivity_scenarios": scenario_rows,
    }


def _power_rows_from_config(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    designs = _validate_designs(config)
    model = _require_mapping(config["power_model"], "power_model")
    scenarios = model["sensitivity_scenarios"]
    assert isinstance(scenarios, list)
    return [_design_row(design, scenarios) for design in designs]


def _minimum_sample_sizes(config: Mapping[str, Any]) -> dict[str, int]:
    model = _require_mapping(config["power_model"], "power_model")
    search = _require_mapping(
        model.get("minimum_sample_search"), "power_model.minimum_sample_search"
    )
    minimum = _validate_n(search.get("minimum_world_count"), "minimum_world_count")
    maximum = _validate_n(search.get("maximum_world_count"), "maximum_world_count")
    strata = search.get("balanced_stratum_count")
    if type(strata) is not int or strata <= 0:
        raise UtilizationPowerError("balanced_stratum_count must be positive")
    unbalanced = minimum_world_count_for_power(
        FROZEN_P_FAVORABLE,
        FROZEN_P_ADVERSE,
        CONSERVATIVE_ALPHA,
        TARGET_POWER,
        minimum_world_count=minimum,
        maximum_world_count=maximum,
    )
    balanced = minimum_world_count_for_power(
        FROZEN_P_FAVORABLE,
        FROZEN_P_ADVERSE,
        CONSERVATIVE_ALPHA,
        TARGET_POWER,
        minimum_world_count=minimum,
        maximum_world_count=maximum,
        required_multiple=strata,
    )
    return {
        "minimum_world_count_unbalanced": unbalanced,
        "minimum_world_count_four_stratum_balanced": balanced,
    }


def _tier_classification(rows: Sequence[Mapping[str, Any]], tier_id: str) -> dict[str, Any]:
    tier_rows = [row for row in rows if row.get("tier_id") == tier_id]
    if not tier_rows:
        raise UtilizationPowerError(f"missing tier {tier_id}")
    passing_designs = [
        str(row["design_id"])
        for row in tier_rows
        if row.get("passes_target_power") is True
    ]
    passed = bool(passing_designs)
    if tier_id == "strict_unique_nonconstant_switch":
        classification = (
            "strict_unique_switch_power_adequate_for_available_geometry"
            if passed
            else "strict_unique_switch_power_inadequate_under_available_geometry"
        )
    else:
        classification = (
            "degraded_two_choice_power_adequate_at_frozen_sesoi"
            if passed
            else "degraded_two_choice_power_inadequate_under_available_geometry"
        )
    return {
        "tier_id": tier_id,
        "design_ids": [row["design_id"] for row in tier_rows],
        "passing_design_ids": passing_designs,
        "at_least_one_design_meets_target_power": passed,
        "classification": classification,
        "tier_mixing_allowed": False,
    }


def _plan_route_family() -> dict[str, Any]:
    return {
        "planned_route_hypothesis_count": 3,
        "family_alpha": fraction_payload(FAMILY_ALPHA),
        "conservative_design_alpha": fraction_payload(CONSERVATIVE_ALPHA),
        "route_power_is_not_jointly_estimated": True,
    }


def _plan_power_model() -> dict[str, Any]:
    return {
        "method": "exact finite binomial enumeration with Fraction arithmetic",
        "conditional_on_non_ties": True,
        "homogeneous_independent_world_working_model": True,
        "stratum_heterogeneity_modeled": False,
        "interpretation": (
            "exact under the frozen homogeneous working alternative only; "
            "not a guarantee under stratum heterogeneity"
        ),
        "frozen_sesoi": {
            "p_favorable": fraction_payload(FROZEN_P_FAVORABLE),
            "p_adverse": fraction_payload(FROZEN_P_ADVERSE),
            "p_tie": fraction_payload(1 - FROZEN_P_FAVORABLE - FROZEN_P_ADVERSE),
            "conditional_favorable_probability": fraction_payload(
                FROZEN_P_FAVORABLE / (FROZEN_P_FAVORABLE + FROZEN_P_ADVERSE)
            ),
        },
        "target_power": fraction_payload(TARGET_POWER),
    }


def build_power_plan(
    config: Mapping[str, Any],
    *,
    source_manifest_sha256: str,
    config_file_sha256: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    artifact_manifest_path: str | Path = DEFAULT_UPSTREAM_MANIFEST_PATH,
    require_current_source: bool = True,
) -> dict[str, Any]:
    """Build a deterministic target-free prospective power plan."""

    loaded_config, observed_config_sha = _load_frozen_config(
        config,
        config_path=config_path,
        expected_file_sha256=config_file_sha256,
    )
    config = loaded_config
    source_freeze_git_head, _ = _check_source_binding(
        source_manifest_sha256,
        require_current_source=require_current_source,
        config_file_sha256=observed_config_sha,
    )
    upstream_binding = _check_upstream_binding(
        config,
        artifact_manifest_path=artifact_manifest_path,
    )
    power_rows = _power_rows_from_config(config)
    tiers = [_tier_classification(power_rows, tier_id) for tier_id in TIER_IDS]
    minimum_samples = _minimum_sample_sizes(config)
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": EVIDENCE_SCOPE,
        "source_manifest_sha256": source_manifest_sha256,
        "source_freeze_git_head": source_freeze_git_head,
        "file_bindings": {
            "config": {
                "relative_path": DEFAULT_CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix(),
                "file_sha256": observed_config_sha,
            },
            "upstream_artifact_manifest": {
                "relative_path": config["upstream_geometry"]["artifact_manifest_relative_path"],
                **upstream_binding,
            },
        },
        "route_family": _plan_route_family(),
        "power_model": _plan_power_model(),
        "candidate_designs": power_rows,
        "tier_results": tiers,
        **minimum_samples,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "final_benchmark_minted": False,
    }
    plan = {**unsigned, "plan_sha256": _sha256_json(unsigned)}
    validate_power_plan(
        config,
        plan,
        config_file_sha256=observed_config_sha,
        config_path=config_path,
        source_manifest_sha256=source_manifest_sha256,
        require_current_source=require_current_source,
        artifact_manifest_path=artifact_manifest_path,
    )
    return plan


build_plan = build_power_plan


def validate_power_plan(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    config_file_sha256: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    source_manifest_sha256: str | None = None,
    require_current_source: bool = True,
    artifact_manifest_path: str | Path = DEFAULT_UPSTREAM_MANIFEST_PATH,
) -> None:
    """Validate a plan's canonical digest, design rows, and file bindings."""

    loaded_config, observed_config_sha = _load_frozen_config(
        config,
        config_path=config_path,
        expected_file_sha256=config_file_sha256,
    )
    config = loaded_config
    if not isinstance(plan, Mapping):
        raise UtilizationPowerError("power plan must be an object")
    expected_top = {
        "schema_version", "kind", "protocol_id", "evidence_scope",
        "source_manifest_sha256", "source_freeze_git_head", "file_bindings",
        "route_family", "power_model",
        "candidate_designs", "tier_results", "minimum_world_count_unbalanced",
        "minimum_world_count_four_stratum_balanced", "model_outputs_read",
        "provider_calls_made", "final_benchmark_minted", "plan_sha256",
    }
    if set(plan) != expected_top:
        raise UtilizationPowerError("power plan uses a non-canonical schema")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if (
        type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("kind") != PLAN_KIND
        or plan.get("protocol_id") != PROTOCOL_ID
        or plan.get("evidence_scope") != EVIDENCE_SCOPE
        or plan.get("plan_sha256") != _sha256_json(unsigned)
        or plan.get("model_outputs_read") is not False
        or type(plan.get("provider_calls_made")) is not int
        or plan.get("provider_calls_made") != 0
        or plan.get("final_benchmark_minted") is not False
    ):
        raise UtilizationPowerError("power plan identity or digest is malformed")
    plan_source = _require_sha256(
        plan.get("source_manifest_sha256"), "plan source manifest"
    )
    frozen_git_head = plan.get("source_freeze_git_head")
    if (
        not isinstance(frozen_git_head, str)
        or len(frozen_git_head) != 40
        or any(character not in "0123456789abcdef" for character in frozen_git_head)
    ):
        raise UtilizationPowerError("power plan source-freeze commit is malformed")
    if (
        source_manifest_sha256 is not None
        and plan_source
        != _require_sha256(source_manifest_sha256, "source_manifest_sha256")
    ):
        raise UtilizationPowerError("power plan source binding differs")
    _, relative_paths = _check_source_binding(
        plan_source,
        require_current_source=require_current_source,
        config_file_sha256=observed_config_sha,
    )
    if require_current_source:
        _require_frozen_commit_matches_source(frozen_git_head, relative_paths)
    bindings = _require_mapping(plan.get("file_bindings"), "power plan file_bindings")
    if set(bindings) != {"config", "upstream_artifact_manifest"}:
        raise UtilizationPowerError("power plan file bindings are non-canonical")
    config_binding = _require_mapping(bindings.get("config"), "power plan config binding")
    upstream_binding = _require_mapping(
        bindings.get("upstream_artifact_manifest"), "power plan upstream binding"
    )
    if set(config_binding) != {"relative_path", "file_sha256"}:
        raise UtilizationPowerError("power plan config binding is non-canonical")
    cfg_sha = _require_sha256(config_binding.get("file_sha256"), "plan config SHA-256")
    if cfg_sha != observed_config_sha:
        raise UtilizationPowerError("power plan config binding differs")
    if config_binding.get("relative_path") != DEFAULT_CONFIG_PATH.relative_to(
        PROJECT_ROOT
    ).as_posix():
        raise UtilizationPowerError("power plan config path drifted")
    expected_upstream = _check_upstream_binding(
        config,
        artifact_manifest_path=artifact_manifest_path,
    )
    if dict(upstream_binding) != {
        "relative_path": config["upstream_geometry"]["artifact_manifest_relative_path"],
        **expected_upstream,
    }:
        raise UtilizationPowerError("power plan upstream binding drifted")
    expected_rows = _power_rows_from_config(config)
    if _canonical_json_bytes(plan.get("candidate_designs")) != _canonical_json_bytes(
        expected_rows
    ):
        raise UtilizationPowerError("power plan design rows drifted")
    expected_tiers = [_tier_classification(expected_rows, tier_id) for tier_id in TIER_IDS]
    if _canonical_json_bytes(plan.get("tier_results")) != _canonical_json_bytes(
        expected_tiers
    ):
        raise UtilizationPowerError("power plan tier classification drifted")
    if _canonical_json_bytes(plan.get("route_family")) != _canonical_json_bytes(
        _plan_route_family()
    ):
        raise UtilizationPowerError("power plan route-family contract drifted")
    if _canonical_json_bytes(plan.get("power_model")) != _canonical_json_bytes(
        _plan_power_model()
    ):
        raise UtilizationPowerError("power plan model contract drifted")
    minimum_samples = _minimum_sample_sizes(config)
    if any(plan.get(key) != value for key, value in minimum_samples.items()):
        raise UtilizationPowerError("power plan minimum sample search drifted")


validate_plan = validate_power_plan


_REVIEWED_PLAN_GUARD = object()


class _ReviewedPlanAuthorization:
    __slots__ = ("plan_sha256", "_guard")

    def __init__(self, plan_sha256: str, guard: object) -> None:
        if guard is not _REVIEWED_PLAN_GUARD or not _is_sha256(plan_sha256):
            raise UtilizationPowerError("reviewed-plan authorization is invalid")
        self.plan_sha256 = plan_sha256
        self._guard = guard


def authorize_reviewed_plan(
    plan: Mapping[str, Any],
    reviewed_plan_sha256: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> _ReviewedPlanAuthorization:
    """Mint the capability required by the result stage after review."""

    expected = _require_sha256(reviewed_plan_sha256, "reviewed_plan_sha256")
    if plan.get("plan_sha256") != expected:
        raise UtilizationPowerError("reviewed plan SHA-256 does not match plan")
    if config is not None:
        validate_power_plan(config, plan)
    else:
        unsigned = {
            key: value for key, value in plan.items() if key != "plan_sha256"
        }
        if plan.get("plan_sha256") != _sha256_json(unsigned):
            raise UtilizationPowerError("reviewed plan digest is malformed")
    return _ReviewedPlanAuthorization(expected, _REVIEWED_PLAN_GUARD)


review_power_plan = authorize_reviewed_plan


def _result_unsigned_from_plan(
    plan: Mapping[str, Any],
    plan_file_sha256: str,
) -> dict[str, Any]:
    plan_file_sha256 = _require_sha256(plan_file_sha256, "plan_file_sha256")
    rows = plan.get("candidate_designs")
    tiers = plan.get("tier_results")
    if not isinstance(rows, list) or not isinstance(tiers, list):
        raise UtilizationPowerError("plan rows are malformed")
    tier_by_id = {
        str(row.get("tier_id")): row
        for row in tiers
        if isinstance(row, Mapping)
    }
    if set(tier_by_id) != set(TIER_IDS):
        raise UtilizationPowerError("plan tier rows are malformed")
    strict_classification = str(
        tier_by_id["strict_unique_nonconstant_switch"]["classification"]
    )
    degraded_classification = str(
        tier_by_id["degraded_two_choice_disjoint_switch"]["classification"]
    )
    overall = (
        "degraded_only_power_gate_passed"
        if tier_by_id["strict_unique_nonconstant_switch"][
            "at_least_one_design_meets_target_power"
        ]
        is False
        and tier_by_id["degraded_two_choice_disjoint_switch"][
            "at_least_one_design_meets_target_power"
        ]
        is True
        else "power_gate_pattern_other"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": EVIDENCE_SCOPE,
        "reviewed_plan_sha256": plan["plan_sha256"],
        "reviewed_plan_file_sha256": plan_file_sha256,
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "source_freeze_git_head": plan["source_freeze_git_head"],
        "file_bindings": plan["file_bindings"],
        "candidate_designs": rows,
        "tier_results": tiers,
        "classification": {
            "strict": strict_classification,
            "degraded": degraded_classification,
            "overall": overall,
            "tier_mixing_allowed": False,
        },
        "minimum_world_count_unbalanced": plan["minimum_world_count_unbalanced"],
        "minimum_world_count_four_stratum_balanced": plan[
            "minimum_world_count_four_stratum_balanced"
        ],
        "exact_rational_gates": {
            "target_power": fraction_payload(TARGET_POWER),
            "conservative_design_alpha": fraction_payload(CONSERVATIVE_ALPHA),
        },
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "final_benchmark_minted": False,
    }


def build_power_result(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    reviewed_plan_sha256: str | None = None,
    reviewed_plan_file_sha256: str | None = None,
    plan_path: str | Path | None = None,
    authorization: _ReviewedPlanAuthorization | None = None,
    config_file_sha256: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    artifact_manifest_path: str | Path = DEFAULT_UPSTREAM_MANIFEST_PATH,
    require_current_source: bool = True,
    source_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic result only after an explicit review barrier."""

    if plan_path is None:
        raise UtilizationPowerError("plan_path is required before result build")
    loaded_plan, observed_plan_file_sha = _read_json_bounded(
        plan_path,
        "power plan",
        maximum_bytes=MAX_POWER_PLAN_BYTES,
        expected_path=DEFAULT_POWER_PLAN_PATH if require_current_source else None,
        expected_name="plan.json",
    )
    if _canonical_json_bytes(loaded_plan) != _canonical_json_bytes(plan):
        raise UtilizationPowerError("plan mapping differs from reviewed plan file bytes")
    plan = loaded_plan
    validate_power_plan(
        config,
        plan,
        config_file_sha256=config_file_sha256,
        config_path=config_path,
        source_manifest_sha256=source_manifest_sha256,
        require_current_source=require_current_source,
        artifact_manifest_path=artifact_manifest_path,
    )
    if reviewed_plan_sha256 is None:
        raise UtilizationPowerError("reviewed_plan_sha256 is required before result build")
    if reviewed_plan_file_sha256 is None:
        raise UtilizationPowerError(
            "reviewed_plan_file_sha256 is required before result build"
        )
    reviewed_file_sha = _require_sha256(
        reviewed_plan_file_sha256, "reviewed_plan_file_sha256"
    )
    if observed_plan_file_sha != reviewed_file_sha:
        raise UtilizationPowerError("reviewed plan file SHA-256 differs from bytes")
    auth = authorize_reviewed_plan(plan, reviewed_plan_sha256)
    if authorization is not None and authorization.plan_sha256 != auth.plan_sha256:
        raise UtilizationPowerError("reviewed-plan authorization does not match plan")
    unsigned = _result_unsigned_from_plan(plan, observed_plan_file_sha)
    return {**unsigned, "result_sha256": _sha256_json(unsigned)}


build_result = build_power_result


def validate_power_result(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    reviewed_plan_sha256: str | None = None,
    reviewed_plan_file_sha256: str | None = None,
    plan_path: str | Path | None = None,
    config_file_sha256: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    artifact_manifest_path: str | Path = DEFAULT_UPSTREAM_MANIFEST_PATH,
    require_current_source: bool = True,
    source_manifest_sha256: str | None = None,
) -> None:
    """Validate a result against operator-reviewed semantic and raw plan hashes."""

    if plan_path is None:
        raise UtilizationPowerError("plan_path is required before result validation")
    loaded_plan, observed_plan_file_sha = _read_json_bounded(
        plan_path,
        "power plan",
        maximum_bytes=MAX_POWER_PLAN_BYTES,
        expected_path=DEFAULT_POWER_PLAN_PATH if require_current_source else None,
        expected_name="plan.json",
    )
    if _canonical_json_bytes(loaded_plan) != _canonical_json_bytes(plan):
        raise UtilizationPowerError("plan mapping differs from reviewed plan file bytes")
    plan = loaded_plan
    validate_power_plan(
        config,
        plan,
        config_file_sha256=config_file_sha256,
        config_path=config_path,
        source_manifest_sha256=source_manifest_sha256,
        require_current_source=require_current_source,
        artifact_manifest_path=artifact_manifest_path,
    )
    if reviewed_plan_sha256 is None:
        raise UtilizationPowerError("reviewed_plan_sha256 is required before result validation")
    if reviewed_plan_file_sha256 is None:
        raise UtilizationPowerError(
            "reviewed_plan_file_sha256 is required before result validation"
        )
    reviewed_file_sha = _require_sha256(
        reviewed_plan_file_sha256, "reviewed_plan_file_sha256"
    )
    if observed_plan_file_sha != reviewed_file_sha:
        raise UtilizationPowerError("reviewed plan file SHA-256 differs from bytes")
    authorize_reviewed_plan(plan, reviewed_plan_sha256)
    if not isinstance(result, Mapping):
        raise UtilizationPowerError("power result must be an object")
    expected_unsigned = _result_unsigned_from_plan(plan, observed_plan_file_sha)
    expected_top = set(expected_unsigned) | {"result_sha256"}
    if set(result) != expected_top:
        raise UtilizationPowerError("power result uses a non-canonical schema")
    unsigned = {key: value for key, value in result.items() if key != "result_sha256"}
    if (
        result.get("result_sha256") != _sha256_json(unsigned)
        or _canonical_json_bytes(unsigned) != _canonical_json_bytes(expected_unsigned)
    ):
        raise UtilizationPowerError("power result identity or digest is malformed")


validate_result = validate_power_result


def _emit_json_exclusive_0600(value: Mapping[str, Any], output: str | Path) -> None:
    """Create one private JSON artifact with mode 0600, never overwriting."""

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise UtilizationPowerError(f"refusing to overwrite artifact {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        raise


def _reject_nonstandard_json_constant(value: str) -> None:
    raise UtilizationPowerError(f"non-standard JSON constant is forbidden: {value}")


def _read_json_bounded(
    path: str | Path,
    label: str,
    *,
    maximum_bytes: int,
    expected_path: str | Path | None = None,
    expected_name: str | None = None,
) -> tuple[dict[str, Any], str]:
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        raise UtilizationPowerError("maximum_bytes must be a positive integer")
    file_path = Path(path)
    if expected_name is not None and file_path.name != expected_name:
        raise UtilizationPowerError(f"{label} must be named {expected_name}")
    if expected_path is not None:
        try:
            if file_path.resolve(strict=True) != Path(expected_path).resolve(strict=True):
                raise UtilizationPowerError(f"{label} path differs from the frozen path")
        except OSError as exc:
            raise UtilizationPowerError(f"cannot resolve {label}") from exc
    try:
        metadata = file_path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise UtilizationPowerError(f"{label} must be a regular file")
        if metadata.st_size > maximum_bytes:
            raise UtilizationPowerError(f"{label} exceeds the pre-read size limit")
        payload = file_path.read_bytes()
        if len(payload) > maximum_bytes:
            raise UtilizationPowerError(f"{label} exceeds the pre-read size limit")
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
        )
    except UtilizationPowerError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UtilizationPowerError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise UtilizationPowerError(f"{label} must be an object")
    return value, _sha256_bytes(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline utilization prospective power")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    plan_parser.add_argument("--output", type=Path, required=True)
    plan_parser.add_argument("--source-manifest-sha256", required=True)
    plan_parser.add_argument(
        "--artifact-manifest",
        type=Path,
        default=DEFAULT_UPSTREAM_MANIFEST_PATH,
    )
    result_parser = subparsers.add_parser("result")
    result_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    result_parser.add_argument("--plan", type=Path, required=True)
    result_parser.add_argument("--output", type=Path, required=True)
    result_parser.add_argument("--reviewed-plan-sha256", required=True)
    result_parser.add_argument("--reviewed-plan-file-sha256", required=True)
    result_parser.add_argument(
        "--artifact-manifest",
        type=Path,
        default=DEFAULT_UPSTREAM_MANIFEST_PATH,
    )
    result_parser.add_argument("--source-manifest-sha256")
    args = parser.parse_args(argv)

    expected_output = (
        DEFAULT_POWER_PLAN_PATH
        if args.command == "plan"
        else DEFAULT_POWER_RESULT_PATH
    )
    if args.output.resolve(strict=False) != expected_output.resolve(strict=False):
        raise UtilizationPowerError(
            f"{args.command} output path differs from the frozen artifact path"
        )

    config, _ = _read_json_bounded(
        args.config,
        "power config",
        maximum_bytes=MAX_CONFIG_BYTES,
        expected_path=DEFAULT_CONFIG_PATH,
    )
    if args.command == "plan":
        result = build_power_plan(
            config,
            config_path=args.config,
            source_manifest_sha256=args.source_manifest_sha256,
            artifact_manifest_path=args.artifact_manifest,
        )
    else:
        plan, _ = _read_json_bounded(
            args.plan,
            "power plan",
            maximum_bytes=MAX_POWER_PLAN_BYTES,
            expected_path=DEFAULT_POWER_PLAN_PATH,
            expected_name="plan.json",
        )
        result = build_power_result(
            config,
            plan,
            reviewed_plan_sha256=args.reviewed_plan_sha256,
            reviewed_plan_file_sha256=args.reviewed_plan_file_sha256,
            plan_path=args.plan,
            config_path=args.config,
            artifact_manifest_path=args.artifact_manifest,
            source_manifest_sha256=args.source_manifest_sha256,
        )
    _emit_json_exclusive_0600(result, args.output)
    return 0


__all__ = [
    "CONFIG_KIND",
    "CONSERVATIVE_ALPHA",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_POWER_PLAN_PATH",
    "DEFAULT_POWER_RESULT_PATH",
    "DEFAULT_UPSTREAM_MANIFEST_PATH",
    "DESIGN_IDS",
    "EVIDENCE_SCOPE",
    "FAMILY_ALPHA",
    "FROZEN_P_ADVERSE",
    "FROZEN_P_FAVORABLE",
    "PLAN_KIND",
    "PROTOCOL_ID",
    "RESULT_KIND",
    "TARGET_POWER",
    "TIER_IDS",
    "UtilizationPowerError",
    "authorize_reviewed_plan",
    "binomial_critical_favorable_count",
    "binomial_tail",
    "binomial_upper_tail",
    "build_plan",
    "build_power_plan",
    "build_power_result",
    "build_result",
    "classify_pair",
    "classify_world",
    "classify_world_scores",
    "critical_favorable_count",
    "exact_binomial_upper_tail",
    "exact_power",
    "exact_sign_critical_count",
    "exact_sign_test",
    "exact_sign_test_power",
    "fraction_payload",
    "main",
    "minimum_world_count_for_power",
    "review_power_plan",
    "summarize_pairs",
    "summarize_world_scores",
    "validate_config",
    "validate_plan",
    "validate_power_plan",
    "validate_power_result",
    "validate_result",
    "_canonical_json_bytes",
    "_emit_json_exclusive_0600",
    "_fraction_from_payload",
    "_fraction_payload",
    "_sha256_bytes",
    "_sha256_json",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
