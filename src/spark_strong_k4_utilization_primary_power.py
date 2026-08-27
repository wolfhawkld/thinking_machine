"""Offline prospective power protocol for one frozen primary route.

This protocol is deliberately smaller than the earlier utilization-power
design.  It has one confirmatory hypothesis, one primary route, and evaluates
only the two strict unique-action geometries that are available from the safe
development scan.  It computes operating characteristics only: no provider,
model-output, or private geometry payload is imported or opened here.

All probability calculations are delegated to
``spark_strong_k4_utilization_power``.  That module is the single arithmetic
implementation and uses exact :class:`fractions.Fraction` enumeration.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from . import spark_strong_k4_utilization_power as _base
from .provenance import PROJECT_ROOT, source_manifest


SCHEMA_VERSION = 1
PROTOCOL_ID = "spark-strong-k4-utilization-primary-route-power-v1"
CONFIG_KIND = "spark-strong-k4-utilization-primary-route-power-config"
PLAN_KIND = "spark-strong-k4-utilization-primary-route-power-plan"
RESULT_KIND = "spark-strong-k4-utilization-primary-route-power-result"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / f"{PROTOCOL_ID}.json"
DEFAULT_UPSTREAM_MANIFEST_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-strong-k4-utilization-feasibility-v2-20260825"
    / "artifact-manifest.json"
)
DEFAULT_UPSTREAM_MANIFEST_RELATIVE_PATH = (
    DEFAULT_UPSTREAM_MANIFEST_PATH.relative_to(PROJECT_ROOT).as_posix()
)
DEFAULT_POWER_ARTIFACT_DIRECTORY = (
    PROJECT_ROOT / "artifacts" / f"{PROTOCOL_ID}-20260827"
)
DEFAULT_POWER_PLAN_PATH = DEFAULT_POWER_ARTIFACT_DIRECTORY / "plan.json"
DEFAULT_POWER_RESULT_PATH = DEFAULT_POWER_ARTIFACT_DIRECTORY / "result.json"

FAMILY_ALPHA = _base.FAMILY_ALPHA
PRIMARY_ALPHA = FAMILY_ALPHA
TARGET_POWER = _base.TARGET_POWER
FROZEN_P_FAVORABLE = _base.FROZEN_P_FAVORABLE
FROZEN_P_ADVERSE = _base.FROZEN_P_ADVERSE
FROZEN_P_TIE = Fraction(3, 10)
FROZEN_CONDITIONAL_FAVORABLE = Fraction(6, 7)
FROZEN_NET_DIRECTION = Fraction(1, 2)

PRIMARY_ROUTE_ID = "deepseek-pro"
PRIMARY_PROVIDER_PROFILE = "deepseek-official-openai-compatible"
PRIMARY_REQUEST_MODEL = "deepseek-v4-pro"
PRIMARY_RESPONSE_MODEL = "deepseek-v4-pro"
PRIMARY_ROUTE_BINDING_SHA256 = (
    "d44699c6e1463c8f428c72e04585feac9cdaf20cd64a680109b1e4d1d9255936"
)
# Compatibility aliases make the frozen alpha/route choices easy to discover
# without introducing another source of values.
ALPHA = PRIMARY_ALPHA
CONFIRMATORY_ALPHA = PRIMARY_ALPHA
POWER_ARTIFACT_DIRECTORY = DEFAULT_POWER_ARTIFACT_DIRECTORY
DEFAULT_PRIMARY_POWER_ARTIFACT_DIRECTORY = DEFAULT_POWER_ARTIFACT_DIRECTORY
EXPLORATORY_ROUTE_IDS = ("deepseek-flash", "glm-5.2")
DESIGN_IDS = ("strict-fallback-q4", "strict-maximum-q6")
TIER_IDS = ("strict_unique_nonconstant_switch",)

EVIDENCE_SCOPE = (
    "offline_prospective_confirmatory_primary_route_power_for_outcome_conditioned_"
    "strict_challenge_only"
)
PRIMARY_CLAIM = (
    "paired net context-responsive unique-action utilization on the selected "
    "finite-DSL outcome-conditioned strict challenge for the frozen deepseek-pro "
    "primary route only; complete two-arm switching is secondary"
)


class PrimaryPowerError(ValueError):
    """Raised when a primary-route prospective-power input fails closed."""


# Keep the historical exception spelling available to callers that share
# validation helpers between the two offline protocols.
UtilizationPowerError = PrimaryPowerError


# Public arithmetic aliases retain the familiar power-v1 API while leaving the
# implementation and constants in the old module untouched.
fraction_payload = _base.fraction_payload
_fraction_payload = _base._fraction_payload
binomial_upper_tail = _base.binomial_upper_tail
exact_binomial_upper_tail = _base.exact_binomial_upper_tail
binomial_tail = _base.binomial_tail
critical_favorable_count = _base.critical_favorable_count
binomial_critical_favorable_count = _base.binomial_critical_favorable_count
exact_sign_critical_count = _base.exact_sign_critical_count
exact_power = _base.exact_power
exact_sign_test_power = _base.exact_sign_test_power
binomial_sign_test_power = _base.binomial_sign_test_power
minimum_world_count_for_power = _base.minimum_world_count_for_power
exact_sign_test = _base.exact_sign_test
classify_world_scores = _base.classify_world_scores
classify_world = _base.classify_world
classify_pair = _base.classify_pair
summarize_world_scores = _base.summarize_world_scores
summarize_pairs = _base.summarize_pairs


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
        raise PrimaryPowerError("value is not canonical JSON") from exc


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
        raise PrimaryPowerError(f"{label} must be a lowercase SHA-256")
    return str(value)


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PrimaryPowerError(f"{label} must be an object")
    return value


def _fraction_from_payload(value: object, label: str) -> Fraction:
    try:
        return _base._fraction_from_payload(value, label)
    except (ValueError, TypeError) as exc:
        raise PrimaryPowerError(str(exc)) from exc


def _require_fraction(value: object, label: str) -> Fraction:
    if not isinstance(value, Fraction):
        raise PrimaryPowerError(f"{label} must be an exact Fraction")
    return value


def _validate_n(value: object, label: str = "n") -> int:
    if type(value) is not int or value < 0:
        raise PrimaryPowerError(f"{label} must be a non-negative integer")
    return value


def _read_json_file(
    path: str | Path,
    label: str,
) -> tuple[dict[str, Any], str]:
    """Read one protocol JSON object and return its raw-byte digest."""

    file_path = Path(path)
    try:
        payload = file_path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrimaryPowerError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise PrimaryPowerError(f"{label} must be an object")
    return value, _sha256_bytes(payload)


def _expected_fraction(section: Mapping[str, Any], label: str, expected: Fraction) -> None:
    observed = _fraction_from_payload(section, label)
    if observed != expected:
        raise PrimaryPowerError(f"{label} drifted")


def _validate_upstream_geometry(config: Mapping[str, Any]) -> Mapping[str, Any]:
    upstream = _require_mapping(config.get("upstream_geometry"), "upstream_geometry")
    string_fields = {
        "artifact_manifest_relative_path",
        "artifact_manifest_file_sha256",
        "artifact_manifest_sha256",
        "upstream_plan_sha256",
        "upstream_scan_sha256",
    }
    for key in string_fields:
        value = upstream.get(key)
        if key.endswith("sha256"):
            _require_sha256(value, f"upstream_geometry.{key}")
        elif value != DEFAULT_UPSTREAM_MANIFEST_RELATIVE_PATH:
            raise PrimaryPowerError(f"upstream_geometry.{key} is malformed")
    expected = {
        "upstream_world_count": 1024,
        "upstream_status": "complete_outcome_conditioned_development_scan",
        "private_pair_or_target_identity_read_by_power_stage": False,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "final_benchmark_minted": False,
    }
    if any(upstream.get(key) != value for key, value in expected.items()):
        raise PrimaryPowerError("upstream geometry safety boundary drifted")
    return upstream


def _validate_designs(config: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    designs = config.get("candidate_designs")
    if not isinstance(designs, list) or len(designs) != len(DESIGN_IDS):
        raise PrimaryPowerError("candidate_designs must contain exactly two designs")
    if [row.get("design_id") for row in designs if isinstance(row, Mapping)] != list(
        DESIGN_IDS
    ):
        raise PrimaryPowerError("candidate design order drifted")
    expected = (
        (
            "strict_unique_nonconstant_switch",
            4,
            16,
            "selected_fallback_geometry",
            1,
            False,
        ),
        (
            "strict_unique_nonconstant_switch",
            6,
            24,
            "maximum_exact_balanced_capacity_not_selected_by_the_feasibility_fallback_rule",
            1,
            True,
        ),
    )
    result: list[Mapping[str, Any]] = []
    for row, expected_row in zip(designs, expected, strict=True):
        if not isinstance(row, Mapping):
            raise PrimaryPowerError("candidate design row is malformed")
        tier, q, n, geometry, set_size, rematching = expected_row
        if (
            row.get("tier_id"),
            row.get("balanced_q"),
            row.get("world_count"),
            row.get("geometry_status"),
            row.get("correct_set_size_each_arm"),
            row.get("claim_limit"),
            row.get("later_sealed_matching_required"),
        ) != (tier, q, n, geometry, set_size, PRIMARY_CLAIM, rematching):
            raise PrimaryPowerError("candidate design constants drifted")
        result.append(row)
    return tuple(result)


def _validate_route_family(config: Mapping[str, Any]) -> Mapping[str, Any]:
    route = _require_mapping(config.get("route_family"), "route_family")
    primary = _require_mapping(route.get("primary_route"), "route_family.primary_route")
    expected_primary = {
        "route_id": PRIMARY_ROUTE_ID,
        "role": "confirmatory_primary",
        "provider_profile": PRIMARY_PROVIDER_PROFILE,
        "request_model": PRIMARY_REQUEST_MODEL,
        "response_model": PRIMARY_RESPONSE_MODEL,
        "route_binding_sha256": PRIMARY_ROUTE_BINDING_SHA256,
        "selection_rationale": (
            "pre-specified highest capability tier before any new cohort or model "
            "outcome; not outcome-selected"
        ),
    }
    if dict(primary) != expected_primary:
        raise PrimaryPowerError("primary route binding drifted")
    exploratory = route.get("exploratory_routes")
    if not isinstance(exploratory, list) or [
        row.get("route_id") for row in exploratory if isinstance(row, Mapping)
    ] != list(EXPLORATORY_ROUTE_IDS):
        raise PrimaryPowerError("exploratory route list drifted")
    expected_exploratory = (
        ("deepseek-flash", "deepseek-v4-flash"),
        ("glm-5.2", "glm-5.2"),
    )
    for row, (route_id, model) in zip(exploratory, expected_exploratory, strict=True):
        if not isinstance(row, Mapping) or dict(row) != {
            "route_id": route_id,
            "role": "exploratory_only",
            "request_model": model,
            "response_model": model,
            "can_replace_primary": False,
            "can_generate_confirmatory_claim": False,
        }:
            raise PrimaryPowerError("exploratory route contract drifted")
    expected_flags = {
        "single_confirmatory_hypothesis": True,
        "planned_hypothesis_count": 1,
        "family_alpha": fraction_payload(FAMILY_ALPHA),
        "primary_alpha": fraction_payload(PRIMARY_ALPHA),
        "holm_applied": False,
        "multiplicity_method": "none; one confirmatory primary hypothesis",
        "fallback_route_forbidden": True,
        "route_identities_frozen_by_this_power_stage": True,
        "exploratory_routes_can_replace_primary": False,
        "exploratory_routes_can_generate_confirmatory_claim": False,
    }
    for key, expected in expected_flags.items():
        observed = route.get(key)
        if key.endswith("alpha"):
            _expected_fraction(_require_mapping(observed, key), key, FAMILY_ALPHA)
        elif observed != expected:
            raise PrimaryPowerError("route-family contract drifted")
    return route


def _validate_estimand(config: Mapping[str, Any]) -> Mapping[str, Any]:
    estimand = _require_mapping(config.get("primary_estimand"), "primary_estimand")
    expected = {
        "independent_unit": "unique development world and its two-context pair",
        "calls_are_not_independent_units": True,
        "routes_may_not_be_pooled_as_independent_worlds": True,
        "arm_score": (
            "one iff the chosen raw action belongs to that arm's frozen "
            "nonconstant-K4 correct set; otherwise zero"
        ),
        "cross_score": (
            "rescore each arm's chosen raw action against the other arm's frozen "
            "disjoint correct set"
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
            "arm-label swap within each world under the null; independent identically "
            "distributed arm outcomes are sufficient but not necessary"
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
            "same prompt wording and response contract apart from the rendered context",
            (
                "pre-live justification and canary of joint arm-exchangeability for "
                "received status, validity and parsed choice; if it is not defensible, "
                "the sign-test gate is not valid"
            ),
        ],
        "invalid_received_response": (
            "if either arm has a received but invalid response, classify the entire "
            "world as a primary tie and a complete-switch miss; keep it in the fixed "
            "denominator"
        ),
        "transport_or_missing_response": (
            "the affected complete route attempt is non-evaluable; no retry, "
            "replacement world, or partial denominator"
        ),
        "tier_mixing_allowed": False,
        "claim_limit": PRIMARY_CLAIM,
    }
    if any(estimand.get(key) != value for key, value in expected.items()):
        raise PrimaryPowerError("primary estimand contract drifted")
    return estimand


def _validate_hypothesis(config: Mapping[str, Any]) -> Mapping[str, Any]:
    hypothesis = _require_mapping(config.get("primary_hypothesis"), "primary_hypothesis")
    expected = {
        "hypothesis_id": "primary_route_strict_unique_action_utilization",
        "confirmatory": True,
        "route_id": PRIMARY_ROUTE_ID,
        "tier_id": "strict_unique_nonconstant_switch",
        "claim": PRIMARY_CLAIM,
        "direction": "positive paired net world-level direction",
        "single_confirmatory_hypothesis": True,
    }
    if dict(hypothesis) != expected:
        raise PrimaryPowerError("primary hypothesis drifted")
    return hypothesis


def _validate_power_model(config: Mapping[str, Any]) -> Mapping[str, Any]:
    model = _require_mapping(config.get("power_model"), "power_model")
    expected_identity = {
        "method": "exact finite binomial enumeration with Fraction arithmetic",
        "conditional_on_non_ties": True,
        "independent_homogeneous_working_model": True,
        "stratum_heterogeneity_modeled": False,
        "gate_uses_primary_alpha": True,
        "gate_uses_only_frozen_sesoi": True,
    }
    for key, expected in expected_identity.items():
        if model.get(key) != expected:
            raise PrimaryPowerError("power-model identity drifted")
    frozen = _require_mapping(model.get("frozen_sesoi"), "power_model.frozen_sesoi")
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
        FROZEN_P_TIE,
    )
    _expected_fraction(
        _require_mapping(
            frozen.get("conditional_favorable_probability"), "conditional theta"
        ),
        "conditional theta",
        FROZEN_CONDITIONAL_FAVORABLE,
    )
    _expected_fraction(
        _require_mapping(frozen.get("net_favorable_minus_adverse"), "net direction"),
        "net direction",
        FROZEN_NET_DIRECTION,
    )
    _expected_fraction(
        _require_mapping(model.get("target_power"), "target_power"),
        "target_power",
        TARGET_POWER,
    )
    search = _require_mapping(
        model.get("minimum_sample_search"), "power_model.minimum_sample_search"
    )
    if (
        search.get("minimum_world_count") != 1
        or search.get("maximum_world_count") != 128
        or search.get("balanced_stratum_count") != 4
    ):
        raise PrimaryPowerError("power-model search contract drifted")
    scenarios = model.get("sensitivity_scenarios")
    expected_scenarios = {
        "weak": (Fraction(2, 5), Fraction(1, 5)),
        "moderate": (Fraction(1, 2), Fraction(3, 20)),
        "frozen-sesoi": (FROZEN_P_FAVORABLE, FROZEN_P_ADVERSE),
        "strong": (Fraction(7, 10), Fraction(1, 20)),
        "near-ideal": (Fraction(4, 5), Fraction(0, 1)),
    }
    if not isinstance(scenarios, list) or [
        row.get("scenario_id") for row in scenarios if isinstance(row, Mapping)
    ] != list(expected_scenarios):
        raise PrimaryPowerError("power sensitivity scenarios drifted")
    for row in scenarios:
        if not isinstance(row, Mapping):
            raise PrimaryPowerError("sensitivity scenario is malformed")
        sid = str(row["scenario_id"])
        pf = _fraction_from_payload(row.get("p_favorable"), f"scenario {sid} p_favorable")
        pa = _fraction_from_payload(row.get("p_adverse"), f"scenario {sid} p_adverse")
        if (pf, pa) != expected_scenarios[sid]:
            raise PrimaryPowerError(f"scenario {sid} constants drifted")
    return model


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate the narrow, frozen primary-route configuration."""

    expected_sections = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence_scope",
        "upstream_geometry",
        "candidate_designs",
        "primary_hypothesis",
        "primary_estimand",
        "route_family",
        "power_model",
        "secondary_endpoints",
        "go_no_go",
        "artifact_contract",
    }
    if not isinstance(config, Mapping) or set(config) != expected_sections:
        raise PrimaryPowerError("power config uses a non-canonical schema")
    if (
        config.get("schema_version") != SCHEMA_VERSION
        or config.get("kind") != CONFIG_KIND
        or config.get("protocol_id") != PROTOCOL_ID
        or config.get("evidence_scope") != EVIDENCE_SCOPE
    ):
        raise PrimaryPowerError("power config identity drifted")
    _validate_upstream_geometry(config)
    _validate_designs(config)
    _validate_hypothesis(config)
    _validate_estimand(config)
    _validate_route_family(config)
    _validate_power_model(config)

    secondary = _require_mapping(config.get("secondary_endpoints"), "secondary_endpoints")
    expected_secondary = {
        "endpoint": "complete context-concordant two-arm switching",
        "role": "secondary",
        "inferential_status": "not the confirmatory hypothesis",
        "route_specific_baselines": "not identified from the safe manifest",
        "uniform_calibration_is_primary_inference": False,
    }
    if dict(secondary) != expected_secondary:
        raise PrimaryPowerError("secondary endpoint boundary drifted")

    go = _require_mapping(config.get("go_no_go"), "go_no_go")
    expected_go = {
        "per_design_pass_rule": (
            "exact primary power at frozen SESOI and primary alpha is at least "
            "target_power"
        ),
        "q4_expected_pass": False,
        "q6_expected_pass": True,
        "strict_only": True,
        "degraded_relabel_forbidden": True,
        "exploratory_route_substitution_forbidden": True,
        "passing_power_mints_final_benchmark": False,
    }
    if dict(go) != expected_go:
        raise PrimaryPowerError("go/no-go boundary drifted")

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
        "interpretation_limit": (
            "only the selected finite-DSL outcome-conditioned strict challenge and "
            "the frozen deepseek-pro primary route; not a model-general, cross-route, "
            "entropy-causal, or natural-opportunity-rate claim"
        ),
    }
    if dict(artifact) != expected_artifact:
        raise PrimaryPowerError("artifact contract drifted")


def _load_frozen_config(
    config: Mapping[str, Any],
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    expected_file_sha256: str | None = None,
) -> tuple[dict[str, Any], str]:
    loaded, observed_sha = _read_json_file(config_path, "power config")
    if expected_file_sha256 is not None and observed_sha != _require_sha256(
        expected_file_sha256, "config_file_sha256"
    ):
        raise PrimaryPowerError("supplied config file SHA-256 differs from bytes")
    if _canonical_json_bytes(loaded) != _canonical_json_bytes(config):
        raise PrimaryPowerError("config mapping differs from frozen config file bytes")
    validate_config(loaded)
    return loaded, observed_sha


def _current_source_state() -> tuple[str, str, list[str], str]:
    observed = source_manifest(PROJECT_ROOT)
    manifest_sha = _require_sha256(
        observed.get("source_manifest_sha256"), "current source manifest"
    )
    environment = _require_mapping(observed.get("environment"), "current source environment")
    git_head = environment.get("git_head")
    if (
        not isinstance(git_head, str)
        or len(git_head) != 40
        or any(character not in "0123456789abcdef" for character in git_head)
    ):
        raise PrimaryPowerError("current source is not bound to a Git commit")
    files = observed.get("files")
    if not isinstance(files, list) or not files:
        raise PrimaryPowerError("current source manifest has no files")
    relative_paths: list[str] = []
    config_relative = DEFAULT_CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix()
    config_entry_sha: str | None = None
    for row in files:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise PrimaryPowerError("current source manifest file row is malformed")
        relative_path = str(row["path"])
        relative_paths.append(relative_path)
        if relative_path == config_relative:
            config_entry_sha = _require_sha256(
                row.get("sha256"), "current source config entry"
            )
    if config_entry_sha is None:
        raise PrimaryPowerError("current source manifest omits the power config")
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
        raise PrimaryPowerError("cannot verify the source-freeze commit") from exc
    if completed.stdout.strip():
        raise PrimaryPowerError(
            "protocol source is not clean relative to the source-freeze commit"
        )


def _require_frozen_commit_matches_source(
    frozen_git_head: str, relative_paths: Sequence[str]
) -> None:
    if (
        len(frozen_git_head) != 40
        or any(character not in "0123456789abcdef" for character in frozen_git_head)
    ):
        raise PrimaryPowerError("source-freeze Git commit is malformed")
    for command in (
        ["git", "merge-base", "--is-ancestor", frozen_git_head, "HEAD"],
        ["git", "diff", "--quiet", frozen_git_head, "--", *relative_paths],
    ):
        try:
            subprocess.run(command, cwd=PROJECT_ROOT, check=True, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PrimaryPowerError(
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
            raise PrimaryPowerError("source manifest drifted")
        if config_file_sha256 is not None and source_config_sha != _require_sha256(
            config_file_sha256, "config_file_sha256"
        ):
            raise PrimaryPowerError("source manifest config binding drifted")
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
    artifact_manifest, observed_file = _read_json_file(
        artifact_manifest_path,
        "safe artifact manifest",
    )
    if observed_file != expected_file:
        raise PrimaryPowerError("upstream artifact-manifest bytes differ")
    unsigned = {key: value for key, value in artifact_manifest.items() if key != "manifest_sha256"}
    if (
        artifact_manifest.get("manifest_sha256") != expected_inner
        or _sha256_json(unsigned) != expected_inner
    ):
        raise PrimaryPowerError("upstream artifact manifest digest mismatch")
    artifacts = _require_mapping(artifact_manifest.get("artifacts"), "artifact manifest artifacts")
    plan_meta = _require_mapping(artifacts.get("plan"), "artifact manifest plan metadata")
    result_meta = _require_mapping(
        artifacts.get("result_raw_private"), "artifact manifest result metadata"
    )
    bindings = _require_mapping(artifact_manifest.get("bindings"), "artifact manifest bindings")
    if (
        plan_meta.get("plan_sha256") != upstream["upstream_plan_sha256"]
        or bindings.get("plan_sha256") != upstream["upstream_plan_sha256"]
        or result_meta.get("scan_sha256") != expected_scan
        or bindings.get("scan_sha256") != expected_scan
    ):
        raise PrimaryPowerError("upstream plan or scan binding mismatch")
    safety = _require_mapping(artifact_manifest.get("safety"), "artifact manifest safety")
    if (
        safety.get("model_outputs_read") is not False
        or safety.get("provider_calls_made") != 0
        or safety.get("final_benchmark_minted") is not False
    ):
        raise PrimaryPowerError("upstream artifact safety boundary drifted")
    return {
        "artifact_manifest_file_sha256": expected_file,
        "artifact_manifest_sha256": expected_inner,
        "upstream_scan_sha256": expected_scan,
        "upstream_plan_sha256": str(upstream["upstream_plan_sha256"]),
    }


def _design_row(
    design: Mapping[str, Any], scenarios: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    n = int(design["world_count"])
    scenario_rows: list[dict[str, Any]] = []
    frozen_power: Fraction | None = None
    for scenario in scenarios:
        sid = str(scenario["scenario_id"])
        pf = _fraction_from_payload(scenario["p_favorable"], f"scenario {sid} p_favorable")
        pa = _fraction_from_payload(scenario["p_adverse"], f"scenario {sid} p_adverse")
        power = exact_power(n, pf, pa, PRIMARY_ALPHA)
        scenario_rows.append({
            "scenario_id": sid,
            "p_favorable": fraction_payload(pf),
            "p_adverse": fraction_payload(pa),
            "exact_power": fraction_payload(power),
        })
        if sid == "frozen-sesoi":
            frozen_power = power
    if frozen_power is None:
        raise PrimaryPowerError("frozen-sesoi scenario is missing")
    return {
        "design_id": design["design_id"],
        "tier_id": design["tier_id"],
        "balanced_q": design["balanced_q"],
        "world_count": n,
        "geometry_status": design["geometry_status"],
        "correct_set_size_each_arm": design["correct_set_size_each_arm"],
        "claim_limit": design["claim_limit"],
        "later_sealed_matching_required": design["later_sealed_matching_required"],
        "primary_alpha": fraction_payload(PRIMARY_ALPHA),
        "frozen_sesoi_exact_power": fraction_payload(frozen_power),
        "target_power": fraction_payload(TARGET_POWER),
        "passes_target_power": frozen_power >= TARGET_POWER,
        "sensitivity_scenarios": scenario_rows,
    }


def _power_rows_from_config(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    designs = _validate_designs(config)
    model = _validate_power_model(config)
    scenarios = model.get("sensitivity_scenarios")
    if not isinstance(scenarios, list):
        raise PrimaryPowerError("power sensitivity scenarios are malformed")
    return [_design_row(design, scenarios) for design in designs]


def _minimum_sample_sizes(config: Mapping[str, Any]) -> dict[str, int]:
    model = _validate_power_model(config)
    search = _require_mapping(
        model.get("minimum_sample_search"), "power_model.minimum_sample_search"
    )
    minimum = _validate_n(search.get("minimum_world_count"), "minimum_world_count")
    maximum = _validate_n(search.get("maximum_world_count"), "maximum_world_count")
    strata = search.get("balanced_stratum_count")
    if type(strata) is not int or strata <= 0:
        raise PrimaryPowerError("balanced_stratum_count must be positive")
    unbalanced = minimum_world_count_for_power(
        FROZEN_P_FAVORABLE,
        FROZEN_P_ADVERSE,
        PRIMARY_ALPHA,
        TARGET_POWER,
        minimum_world_count=minimum,
        maximum_world_count=maximum,
    )
    balanced = minimum_world_count_for_power(
        FROZEN_P_FAVORABLE,
        FROZEN_P_ADVERSE,
        PRIMARY_ALPHA,
        TARGET_POWER,
        minimum_world_count=minimum,
        maximum_world_count=maximum,
        required_multiple=strata,
    )
    return {
        "minimum_world_count_unbalanced": unbalanced,
        "minimum_world_count_four_stratum_balanced": balanced,
    }


def _tier_classification(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    passing = [str(row["design_id"]) for row in rows if row.get("passes_target_power") is True]
    return {
        "tier_id": "strict_unique_nonconstant_switch",
        "design_ids": [row["design_id"] for row in rows],
        "passing_design_ids": passing,
        "at_least_one_design_meets_target_power": bool(passing),
        "classification": (
            "strict_unique_switch_power_adequate_at_q6"
            if passing
            else "strict_unique_switch_power_inadequate_under_available_geometry"
        ),
        "tier_mixing_allowed": False,
    }


def _plan_route_family(config: Mapping[str, Any]) -> dict[str, Any]:
    route = _validate_route_family(config)
    return {
        "primary_route": dict(route["primary_route"]),
        "exploratory_routes": [dict(row) for row in route["exploratory_routes"]],
        "single_confirmatory_hypothesis": True,
        "family_alpha": fraction_payload(FAMILY_ALPHA),
        "primary_alpha": fraction_payload(PRIMARY_ALPHA),
        "holm_applied": False,
        "fallback_route_forbidden": True,
    }


def _plan_power_model(config: Mapping[str, Any]) -> dict[str, Any]:
    model = _validate_power_model(config)
    return {
        "method": model["method"],
        "conditional_on_non_ties": True,
        "independent_homogeneous_working_model": True,
        "stratum_heterogeneity_modeled": False,
        "interpretation": (
            "exact under the frozen homogeneous working alternative only; not a "
            "guarantee under stratum heterogeneity"
        ),
        "frozen_sesoi": dict(model["frozen_sesoi"]),
        "target_power": dict(model["target_power"]),
        "primary_alpha": fraction_payload(PRIMARY_ALPHA),
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
    """Build a deterministic target-free primary-route power plan."""

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
    rows = _power_rows_from_config(config)
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
        "primary_hypothesis": dict(config["primary_hypothesis"]),
        "primary_route": dict(config["route_family"]["primary_route"]),
        "route_family": _plan_route_family(config),
        "power_model": _plan_power_model(config),
        "candidate_designs": rows,
        "tier_result": _tier_classification(rows),
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
build_primary_power_plan = build_power_plan


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
    """Validate canonical plan, source freeze, and safe-upstream bindings."""

    loaded_config, observed_config_sha = _load_frozen_config(
        config,
        config_path=config_path,
        expected_file_sha256=config_file_sha256,
    )
    config = loaded_config
    if not isinstance(plan, Mapping):
        raise PrimaryPowerError("power plan must be an object")
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence_scope",
        "source_manifest_sha256",
        "source_freeze_git_head",
        "file_bindings",
        "primary_hypothesis",
        "primary_route",
        "route_family",
        "power_model",
        "candidate_designs",
        "tier_result",
        "minimum_world_count_unbalanced",
        "minimum_world_count_four_stratum_balanced",
        "model_outputs_read",
        "provider_calls_made",
        "final_benchmark_minted",
        "plan_sha256",
    }
    if set(plan) != expected_top:
        raise PrimaryPowerError("power plan uses a non-canonical schema")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if (
        type(plan.get("schema_version")) is not int
        or plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("kind") != PLAN_KIND
        or plan.get("protocol_id") != PROTOCOL_ID
        or plan.get("evidence_scope") != EVIDENCE_SCOPE
        or plan.get("plan_sha256") != _sha256_json(unsigned)
        or plan.get("model_outputs_read") is not False
        or plan.get("provider_calls_made") != 0
        or plan.get("final_benchmark_minted") is not False
    ):
        raise PrimaryPowerError("power plan identity or digest is malformed")
    plan_source = _require_sha256(plan.get("source_manifest_sha256"), "plan source manifest")
    frozen_git_head = plan.get("source_freeze_git_head")
    if (
        not isinstance(frozen_git_head, str)
        or len(frozen_git_head) != 40
        or any(character not in "0123456789abcdef" for character in frozen_git_head)
    ):
        raise PrimaryPowerError("power plan source-freeze commit is malformed")
    if source_manifest_sha256 is not None and plan_source != _require_sha256(
        source_manifest_sha256, "source_manifest_sha256"
    ):
        raise PrimaryPowerError("power plan source binding differs")
    _, relative_paths = _check_source_binding(
        plan_source,
        require_current_source=require_current_source,
        config_file_sha256=observed_config_sha,
    )
    if require_current_source:
        _require_frozen_commit_matches_source(frozen_git_head, relative_paths)
    bindings = _require_mapping(plan.get("file_bindings"), "power plan file_bindings")
    if set(bindings) != {"config", "upstream_artifact_manifest"}:
        raise PrimaryPowerError("power plan file bindings are non-canonical")
    cfg = _require_mapping(bindings.get("config"), "power plan config binding")
    up = _require_mapping(bindings.get("upstream_artifact_manifest"), "power plan upstream binding")
    if (
        set(cfg) != {"relative_path", "file_sha256"}
        or _require_sha256(cfg.get("file_sha256"), "plan config SHA-256")
        != observed_config_sha
        or cfg.get("relative_path")
        != DEFAULT_CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix()
    ):
        raise PrimaryPowerError("power plan config binding drifted")
    expected_upstream = _check_upstream_binding(
        config,
        artifact_manifest_path=artifact_manifest_path,
    )
    if dict(up) != {
        "relative_path": config["upstream_geometry"]["artifact_manifest_relative_path"],
        **expected_upstream,
    }:
        raise PrimaryPowerError("power plan upstream binding drifted")
    rows = _power_rows_from_config(config)
    if _canonical_json_bytes(plan.get("candidate_designs")) != _canonical_json_bytes(rows):
        raise PrimaryPowerError("power plan design rows drifted")
    if _canonical_json_bytes(plan.get("tier_result")) != _canonical_json_bytes(
        _tier_classification(rows)
    ):
        raise PrimaryPowerError("power plan tier classification drifted")
    if _canonical_json_bytes(plan.get("primary_hypothesis")) != _canonical_json_bytes(
        config["primary_hypothesis"]
    ):
        raise PrimaryPowerError("power plan hypothesis binding drifted")
    if _canonical_json_bytes(plan.get("primary_route")) != _canonical_json_bytes(
        config["route_family"]["primary_route"]
    ):
        raise PrimaryPowerError("power plan primary route binding drifted")
    if _canonical_json_bytes(plan.get("route_family")) != _canonical_json_bytes(
        _plan_route_family(config)
    ):
        raise PrimaryPowerError("power plan route-family contract drifted")
    if _canonical_json_bytes(plan.get("power_model")) != _canonical_json_bytes(
        _plan_power_model(config)
    ):
        raise PrimaryPowerError("power plan model contract drifted")
    minimum_samples = _minimum_sample_sizes(config)
    if any(plan.get(key) != value for key, value in minimum_samples.items()):
        raise PrimaryPowerError("power plan minimum sample search drifted")


validate_plan = validate_power_plan
validate_primary_power_plan = validate_power_plan


def authorize_reviewed_plan(
    plan: Mapping[str, Any],
    reviewed_plan_sha256: str,
    *,
    config: Mapping[str, Any] | None = None,
) -> str:
    """Validate the operator-supplied reviewed semantic digest."""

    expected = _require_sha256(reviewed_plan_sha256, "reviewed_plan_sha256")
    if plan.get("plan_sha256") != expected:
        raise PrimaryPowerError("reviewed plan SHA-256 does not match plan")
    if config is not None:
        validate_power_plan(config, plan)
    else:
        unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
        if plan.get("plan_sha256") != _sha256_json(unsigned):
            raise PrimaryPowerError("reviewed plan digest is malformed")
    return expected


review_power_plan = authorize_reviewed_plan


def _result_unsigned_from_plan(plan: Mapping[str, Any], plan_file_sha256: str) -> dict[str, Any]:
    plan_file_sha256 = _require_sha256(plan_file_sha256, "plan_file_sha256")
    rows = plan.get("candidate_designs")
    tier = plan.get("tier_result")
    if not isinstance(rows, list) or not isinstance(tier, Mapping):
        raise PrimaryPowerError("plan rows are malformed")
    overall = (
        "q6_confirmatory_primary_power_pass_q4_fail"
        if tier.get("passing_design_ids") == ["strict-maximum-q6"]
        else "primary_power_gate_pattern_other"
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
        "primary_hypothesis": plan["primary_hypothesis"],
        "primary_route": plan["primary_route"],
        "candidate_designs": rows,
        "tier_result": tier,
        "classification": {
            "primary_route": PRIMARY_ROUTE_ID,
            "q4": "pass" if rows[0]["passes_target_power"] else "fail",
            "q6": "pass" if rows[1]["passes_target_power"] else "fail",
            "overall": overall,
            "claim_limit": PRIMARY_CLAIM,
        },
        "minimum_world_count_unbalanced": plan["minimum_world_count_unbalanced"],
        "minimum_world_count_four_stratum_balanced": plan[
            "minimum_world_count_four_stratum_balanced"
        ],
        "exact_rational_gates": {
            "target_power": fraction_payload(TARGET_POWER),
            "primary_alpha": fraction_payload(PRIMARY_ALPHA),
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
    config_file_sha256: str | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    artifact_manifest_path: str | Path = DEFAULT_UPSTREAM_MANIFEST_PATH,
    require_current_source: bool = True,
    source_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a result only after a separately supplied reviewed plan file."""

    if plan_path is None:
        raise PrimaryPowerError("plan_path is required before result build")
    loaded_plan, observed_plan_file_sha = _read_json_file(plan_path, "power plan")
    if _canonical_json_bytes(loaded_plan) != _canonical_json_bytes(plan):
        raise PrimaryPowerError("plan mapping differs from reviewed plan file bytes")
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
    if reviewed_plan_sha256 is None or reviewed_plan_file_sha256 is None:
        raise PrimaryPowerError(
            "reviewed plan semantic and file SHA-256 are required before result build"
        )
    reviewed_file_sha = _require_sha256(reviewed_plan_file_sha256, "reviewed_plan_file_sha256")
    if observed_plan_file_sha != reviewed_file_sha:
        raise PrimaryPowerError("reviewed plan file SHA-256 differs from bytes")
    authorize_reviewed_plan(plan, reviewed_plan_sha256)
    unsigned = _result_unsigned_from_plan(plan, observed_plan_file_sha)
    return {**unsigned, "result_sha256": _sha256_json(unsigned)}


build_result = build_power_result
build_primary_power_result = build_power_result


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
    if plan_path is None:
        raise PrimaryPowerError("plan_path is required before result validation")
    loaded_plan, observed_plan_file_sha = _read_json_file(plan_path, "power plan")
    if _canonical_json_bytes(loaded_plan) != _canonical_json_bytes(plan):
        raise PrimaryPowerError("plan mapping differs from reviewed plan file bytes")
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
    if reviewed_plan_sha256 is None or reviewed_plan_file_sha256 is None:
        raise PrimaryPowerError(
            "reviewed plan semantic and file SHA-256 are required before result validation"
        )
    reviewed_file_sha = _require_sha256(reviewed_plan_file_sha256, "reviewed_plan_file_sha256")
    if observed_plan_file_sha != reviewed_file_sha:
        raise PrimaryPowerError("reviewed plan file SHA-256 differs from bytes")
    authorize_reviewed_plan(plan, reviewed_plan_sha256)
    if not isinstance(result, Mapping):
        raise PrimaryPowerError("power result must be an object")
    expected_unsigned = _result_unsigned_from_plan(plan, observed_plan_file_sha)
    expected_top = set(expected_unsigned) | {"result_sha256"}
    if set(result) != expected_top:
        raise PrimaryPowerError("power result uses a non-canonical schema")
    unsigned = {key: value for key, value in result.items() if key != "result_sha256"}
    if (
        result.get("result_sha256") != _sha256_json(unsigned)
        or _canonical_json_bytes(unsigned) != _canonical_json_bytes(expected_unsigned)
    ):
        raise PrimaryPowerError("power result identity or digest is malformed")


validate_result = validate_power_result
validate_primary_power_result = validate_power_result


def _emit_json_exclusive_0600(value: Mapping[str, Any], output: str | Path) -> None:
    """Create one private JSON artifact with mode 0600, never overwriting."""

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise PrimaryPowerError(f"refusing to overwrite artifact {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
    except BaseException:
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline primary-route utilization prospective power"
    )
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
        raise PrimaryPowerError(f"{args.command} output path differs from the frozen artifact path")
    config, _ = _read_json_file(args.config, "power config")
    if args.command == "plan":
        result = build_power_plan(
            config,
            config_path=args.config,
            source_manifest_sha256=args.source_manifest_sha256,
            artifact_manifest_path=args.artifact_manifest,
        )
    else:
        plan, _ = _read_json_file(args.plan, "power plan")
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
    "ALPHA",
    "CONFIG_KIND",
    "CONFIRMATORY_ALPHA",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_POWER_ARTIFACT_DIRECTORY",
    "DEFAULT_PRIMARY_POWER_ARTIFACT_DIRECTORY",
    "DEFAULT_POWER_PLAN_PATH",
    "DEFAULT_POWER_RESULT_PATH",
    "DEFAULT_UPSTREAM_MANIFEST_PATH",
    "DEFAULT_UPSTREAM_MANIFEST_RELATIVE_PATH",
    "DESIGN_IDS",
    "EVIDENCE_SCOPE",
    "EXPLORATORY_ROUTE_IDS",
    "FAMILY_ALPHA",
    "FROZEN_P_ADVERSE",
    "FROZEN_P_FAVORABLE",
    "FROZEN_P_TIE",
    "POWER_ARTIFACT_DIRECTORY",
    "PRIMARY_ALPHA",
    "PRIMARY_CLAIM",
    "PRIMARY_PROVIDER_PROFILE",
    "PRIMARY_REQUEST_MODEL",
    "PRIMARY_RESPONSE_MODEL",
    "PRIMARY_ROUTE_BINDING_SHA256",
    "PRIMARY_ROUTE_ID",
    "PLAN_KIND",
    "PROTOCOL_ID",
    "RESULT_KIND",
    "TARGET_POWER",
    "TIER_IDS",
    "PrimaryPowerError",
    "UtilizationPowerError",
    "authorize_reviewed_plan",
    "binomial_critical_favorable_count",
    "binomial_sign_test_power",
    "binomial_tail",
    "binomial_upper_tail",
    "build_plan",
    "build_power_plan",
    "build_power_result",
    "build_primary_power_plan",
    "build_primary_power_result",
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
    "validate_primary_power_plan",
    "validate_primary_power_result",
    "validate_result",
    "_canonical_json_bytes",
    "_emit_json_exclusive_0600",
    "_fraction_from_payload",
    "_fraction_payload",
    "_read_json_file",
    "_sha256_bytes",
    "_sha256_json",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
