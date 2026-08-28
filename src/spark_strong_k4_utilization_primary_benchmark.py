"""Offline construction of the strict-q6 utilization response benchmark.

The worlds consumed here retain their original outcome-conditioned development
provenance.  What is prospective is only the later, preregistered model-response
test.  This module constructs masked materials; it has no provider boundary and
never reads model responses.

Construction has two deliberately separate phases.  ``build_construction_plan``
reads only tracked, target-free metadata and freezes the 24 pair slots and their
counterbalancing schedule.  ``construct_benchmark`` may open the private shard
files only after the semantic and raw-file hashes of that plan have both been
supplied and checked.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from . import dsl, spark_lineage
from . import spark_strong_k4_benchmark as prompt_support
from . import spark_strong_k4_scan as strong_scan
from . import spark_strong_k4_utilization_feasibility as feasibility
from .provenance import PROJECT_ROOT, protocol_git_pathspecs, source_manifest


CONFIG_SCHEMA_VERSION = 2
CONFIG_CANONICAL_SHA256 = "b57877f71aa716692e6e9623cc0e6e07877d6f93c1a7c68d7ab33690fa4182df"
ARTIFACT_SCHEMA_VERSION = 1
PROTOCOL_ID = "spark-strong-k4-utilization-primary-benchmark-v2"
CONFIG_KIND = "spark-strong-k4-utilization-primary-benchmark-config"
PLAN_KIND = "spark-strong-k4-utilization-primary-benchmark-plan"
PUBLIC_MANIFEST_KIND = "spark-strong-k4-utilization-primary-benchmark-public-manifest"
PRIVATE_KEY_KIND = "spark-strong-k4-utilization-primary-benchmark-private-key"
CONSTRUCTION_RESULT_KIND = "spark-strong-k4-utilization-primary-benchmark-result"

WORLD_LAYER_LABEL = "outcome_conditioned_development_only"
MODEL_RESPONSE_LAYER_LABEL = "preregistered_prospective_primary"
EXPECTED_EVIDENCE_LABELS = {
    "world_layer_label": WORLD_LAYER_LABEL,
    "model_response_layer_label": MODEL_RESPONSE_LAYER_LABEL,
    "independent_heldout_confirmation": False,
    "allowed_primary_result_labels": {
        "significant": (
            "prospective_primary_positive_on_fixed_development_constructed_"
            "finite_DSL_challenge"
        ),
        "not_significant": (
            "prospective_primary_not_detected_on_fixed_development_constructed_"
            "finite_DSL_challenge"
        ),
        "non_evaluable": "prospective_primary_non_evaluable_under_frozen_failure_policy",
    },
    "allowed_secondary_result_labels": [
        "descriptive_complete_switch_on_fixed_development_constructed_finite_DSL_challenge",
        (
            "descriptive_directionally_consistent_case_on_fixed_development_constructed_"
            "finite_DSL_challenge"
        ),
        "descriptive_shortcut_sensitivity_on_fixed_development_constructed_finite_DSL_challenge",
    ],
    "significant_primary_interpretation": (
        "supports paired net context-responsive unique-action utilization by deepseek-pro "
        "on this one fixed development-constructed strict finite-DSL challenge"
    ),
    "forbidden_conclusion_labels": [
        "confirmatory_primary",
        "independent_heldout_confirmation",
        "independent_confirmatory_replication",
        "natural_world_opportunity_rate_estimate",
        "model_general_capability_established",
        "internal_entropy_causality_established",
        "human_unknown_discovery_established",
        "training_external_invention_established",
        "real_world_scientific_discovery_generalization",
    ],
}
EXPECTED_PRIMARY_ROUTE = {
    "route_id": "deepseek-pro",
    "role": MODEL_RESPONSE_LAYER_LABEL,
    "provider_profile": "deepseek-official-openai-compatible",
    "request_model": "deepseek-v4-pro",
    "response_model": "deepseek-v4-pro",
    "route_binding_sha256": (
        "d44699c6e1463c8f428c72e04585feac9cdaf20cd64a680109b1e4d1d9255936"
    ),
    "formal_task_calls": 48,
    "fallback_route_forbidden": True,
    "exploratory_route_execution_decision": (
        "must_be_frozen_in_the_later_live_plan_before_any_primary_call"
    ),
}
EXPECTED_ANALYSIS_BINDING = {
    "primary_hypothesis_id": "primary_route_strict_unique_action_utilization",
    "primary_test": "one-sided exact sign test conditional on non-tie worlds",
    "primary_alpha": {"numerator": 1, "denominator": 20},
    "world_signed_score": (
        "sum of the two own-context correct-set indicators minus the sum of the two "
        "cross-context correct-set indicators"
    ),
    "favorable_world": "world_signed_score > 0",
    "adverse_world": "world_signed_score < 0",
    "tie_world": "world_signed_score = 0",
    "received_invalid_response": (
        "entire world is a primary tie and complete-switch miss in the fixed denominator"
    ),
    "transport_or_missing_response": (
        "complete primary route attempt is non-evaluable with no retry, replacement world "
        "or partial denominator"
    ),
    "complete_two_arm_context_concordant_switch": "secondary",
    "joint_observable_outcome_arm_exchangeability_required": True,
    "hard_balance_alone_establishes_exchangeability": False,
}
EXPECTED_TARGET_BLIND_BASELINES = {
    "computed_only_after_the_24_world_cohort_is_irrevocably_selected": True,
    "may_change_cohort_or_pair_selection": False,
    "inferential_role": (
        "descriptive shortcut sensitivity; not a second primary gate and not used to "
        "select a best model result"
    ),
    "fixed_semantic_policy_count": 10,
    "fixed_display_position_policy_count": 10,
    "public_K1_policy_ids": [
        "first-public-K1-else-first-displayed",
        "public-k1-min-node-hash",
        "public-k1-min-positive-node-hash",
        "public-k1-max-parent-novelty-node-hash",
    ],
    "public_feature_inputs": (
        "target-free D0, parent, context fragment, complete input domain and action "
        "lineage only"
    ),
    "target_K2_K3_K4_or_model_output_may_be_read_by_policy": False,
    "report_each_policy_favorable_adverse_tie_complete_switch_and_signed_total": True,
    "B_star_selection_or_posthoc_thresholding": False,
}
EXPECTED_REMAINING_LIVE_BARRIERS = {
    "new_prompt_target_free_route_canary_required": True,
    "joint_exchangeability_canary_and_justification_required": True,
    "response_contract_and_failure_policy_must_be_sealed": True,
    "exploratory_route_decision_must_be_sealed": True,
    "analysis_contract_must_bind_public_and_private_file_hashes": True,
    "passing_construction_directly_authorizes_provider_calls": False,
}
PAIR_COUNT = 24
TASK_COUNT = 48
RAW_ACTION_COUNT = 10
ARMS = ("context_a", "context_b")
TARGET_PER_STRATUM = 6
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / f"{PROTOCOL_ID}.json"
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "artifacts" / f"{PROTOCOL_ID}-20260827"

_TASK_ID_RE = re.compile(r"TASK-[A-Z2-7]{14}\Z")
_OPTION_ID_RE = re.compile(r"Q[0-9A-F]{8}\Z")


class PrimaryBenchmarkError(ValueError):
    """A frozen input or constructed benchmark is scientifically inconsistent."""


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
        raise PrimaryBenchmarkError("value is not canonical JSON") from exc


def _rendered_json_bytes(value: object) -> bytes:
    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PrimaryBenchmarkError("artifact is not renderable JSON") from exc
    return (rendered + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_sha256(value: object, label: str) -> str:
    if not _is_sha256(value):
        raise PrimaryBenchmarkError(f"{label} must be a lowercase SHA-256")
    return str(value)


def _require_git_commit(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) not in {40, 64}
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PrimaryBenchmarkError(f"{label} must be a lowercase Git commit id")
    return value


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PrimaryBenchmarkError(f"{label} must be an object")
    return value


def _project_path(root: Path, relative_path: object, label: str) -> Path:
    if not isinstance(relative_path, str):
        raise PrimaryBenchmarkError(f"{label} path is malformed")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise PrimaryBenchmarkError(f"{label} must be a project-relative path")
    return root / relative


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrimaryBenchmarkError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise PrimaryBenchmarkError(f"{label} must contain one JSON object")
    return value, payload


def _assert_clean_source_freeze(
    project_root: Path,
    current_source_manifest: Mapping[str, Any],
) -> tuple[str, list[str]]:
    environment = _require_mapping(
        current_source_manifest.get("environment"), "source-manifest environment"
    )
    observed_head = _require_git_commit(environment.get("git_head"), "current Git head")
    files = current_source_manifest.get("files")
    if not isinstance(files, list) or not files:
        raise PrimaryBenchmarkError("source manifest has no protocol files")
    for row in files:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise PrimaryBenchmarkError("source manifest file row is malformed")
    source_pathspecs = list(protocol_git_pathspecs())
    try:
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *source_pathspecs,
            ],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PrimaryBenchmarkError("cannot verify the source-freeze worktree") from exc
    if status.stdout.strip():
        raise PrimaryBenchmarkError(
            "construction plan requires clean protocol source files"
        )
    return observed_head, source_pathspecs


def _assert_frozen_commit_matches_source(
    project_root: Path,
    frozen_git_head: str,
    source_pathspecs: Sequence[str],
) -> None:
    frozen_head = _require_git_commit(frozen_git_head, "source-freeze Git head")
    for command in (
        ["git", "merge-base", "--is-ancestor", frozen_head, "HEAD"],
        ["git", "diff", "--quiet", frozen_head, "--", *source_pathspecs],
    ):
        try:
            subprocess.run(
                command,
                cwd=project_root,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise PrimaryBenchmarkError(
                "source-freeze commit does not match current protocol files"
            ) from exc


def _check_inner_digest(value: Mapping[str, Any], field: str, label: str) -> str:
    observed = _require_sha256(value.get(field), f"{label}.{field}")
    unsigned = {key: item for key, item in value.items() if key != field}
    if _sha256_json(unsigned) != observed:
        raise PrimaryBenchmarkError(f"{label} semantic digest differs")
    return observed


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate the v2 provenance labels and frozen construction constants."""

    if not isinstance(config, Mapping):
        raise PrimaryBenchmarkError("benchmark config must be an object")
    if (
        _sha256_json(config) != CONFIG_CANONICAL_SHA256
        or config.get("schema_version") != CONFIG_SCHEMA_VERSION
        or config.get("kind") != CONFIG_KIND
        or config.get("protocol_id") != PROTOCOL_ID
        or config.get("evidence_scope")
        != "offline_outcome_conditioned_development_strict_q6_masked_benchmark_"
        "construction_for_a_preregistered_prospective_primary_response_test"
    ):
        raise PrimaryBenchmarkError("benchmark v2 identity drifted")

    supersession = _require_mapping(config.get("supersession"), "supersession")
    reuse = _require_mapping(
        config.get("pre_model_human_reuse_decision"), "pre_model_human_reuse_decision"
    )
    labels = _require_mapping(config.get("evidence_labels"), "evidence_labels")
    geometry = _require_mapping(config.get("upstream_geometry"), "upstream_geometry")
    power = _require_mapping(config.get("upstream_power_gate"), "upstream_power_gate")
    cohort = _require_mapping(config.get("cohort_selection"), "cohort_selection")
    strict = _require_mapping(config.get("strict_pair_contract"), "strict_pair_contract")
    masking = _require_mapping(config.get("masking_and_prompt"), "masking_and_prompt")
    schedule = _require_mapping(config.get("schedule"), "schedule")
    route = _require_mapping(config.get("primary_route"), "primary_route")
    analysis = _require_mapping(config.get("analysis_binding"), "analysis_binding")
    baselines = _require_mapping(
        config.get("target_blind_structural_baselines"),
        "target_blind_structural_baselines",
    )
    barrier = _require_mapping(
        config.get("construction_plan_barrier"), "construction_plan_barrier"
    )
    artifact = _require_mapping(config.get("artifact_contract"), "artifact_contract")
    live = _require_mapping(config.get("remaining_live_barriers"), "remaining_live_barriers")

    if (
        supersession.get("supersedes_protocol_id")
        != "spark-strong-k4-utilization-primary-benchmark-v1"
        or supersession.get("effective_before_any_benchmark_mint_or_live_call") is not True
        or supersession.get("v1_must_not_be_used_to_mint_or_run_the_benchmark") is not True
        or supersession.get("benchmark_minted_before_this_decision") is not False
        or supersession.get("benchmark_provider_calls_made_before_this_decision") != 0
        or supersession.get("benchmark_model_outputs_read_before_this_decision") is not False
    ):
        raise PrimaryBenchmarkError("v2 supersession boundary drifted")
    if (
        reuse.get("world_sampling_status") != WORLD_LAYER_LABEL
        or reuse.get("reserved_worlds_are_development_only_forever") is not True
        or reuse.get("natural_sample_claim_allowed") is not False
        or reuse.get("independent_heldout_sample_claim_allowed") is not False
        or reuse.get("model_response_layer_role") != MODEL_RESPONSE_LAYER_LABEL
        or reuse.get("private_shards_read_during_this_amendment") is not False
        or reuse.get("selection_may_use_benchmark_model_output") is not False
    ):
        raise PrimaryBenchmarkError("development-world reuse decision drifted")
    if dict(labels) != EXPECTED_EVIDENCE_LABELS:
        raise PrimaryBenchmarkError("evidence labels drifted")

    for key in (
        "artifact_manifest_file_sha256",
        "artifact_manifest_sha256",
        "feasibility_config_file_sha256",
        "feasibility_plan_file_sha256",
        "feasibility_plan_sha256",
        "feasibility_scan_sha256",
        "shard_files_manifest_sha256",
    ):
        _require_sha256(geometry.get(key), f"upstream_geometry.{key}")
    for key in (
        "config_file_sha256",
        "plan_file_sha256",
        "plan_sha256",
        "result_file_sha256",
        "result_sha256",
    ):
        _require_sha256(power.get(key), f"upstream_power_gate.{key}")
    for section, keys in (
        (
            geometry,
            (
                "artifact_manifest_relative_path",
                "feasibility_config_relative_path",
                "feasibility_plan_relative_path",
            ),
        ),
        (
            power,
            ("config_relative_path", "plan_relative_path", "result_relative_path"),
        ),
        (supersession, ("superseded_config_relative_path",)),
    ):
        for key in keys:
            _project_path(PROJECT_ROOT, section.get(key), key)

    strata = list(spark_lineage.MOTIF_STRATA)
    if (
        geometry.get("shard_count") != 128
        or geometry.get("shard_world_count") != 8
        or geometry.get("world_count") != 1024
        or geometry.get("monolithic_result_must_not_be_read") is not True
        or geometry.get("all_shards_must_be_hash_and_schema_validated") is not True
        or power.get("passing_design_id") != "strict-maximum-q6"
        or power.get("required_tier_classification")
        != "strict_unique_switch_power_adequate_at_q6"
        or power.get("historical_overall_classification_role")
        != "exact numeric power-gate provenance only"
        or power.get("confirmatory_wording_in_historical_classification_is_inherited")
        is not False
        or power.get("power_is_model_evidence") is not False
    ):
        raise PrimaryBenchmarkError("upstream geometry or power gate drifted")
    if (
        cohort.get("tier_id") != feasibility.STRICT_TIER
        or cohort.get("target_per_stratum") != TARGET_PER_STRATUM
        or cohort.get("fallback_per_stratum") != TARGET_PER_STRATUM
        or cohort.get("world_count") != PAIR_COUNT
        or cohort.get("task_count") != TASK_COUNT
        or cohort.get("construction_strata_in_frozen_order") != strata
        or cohort.get("world_capacity") != 1
        or cohort.get("selected_worlds_remain_development_only") is not True
        or cohort.get("selection_may_read_provider_route_or_model_output") is not False
        or cohort.get("selection_may_optimize_display_position") is not False
        or cohort.get("selection_may_use_manual_scientific_attractiveness") is not False
    ):
        raise PrimaryBenchmarkError("strict q6 cohort contract drifted")
    expected_strict = {
        "same_world_D0_parent_and_action_universe": True,
        "same_motif_stratum_and_complexity": True,
        "distinct_motif_id_and_complete_domain_behavior": True,
        "K2_opportunity_counts_equal": True,
        "constant_K4_count_each_arm": 0,
        "nonconstant_K4_correct_raw_action_count_each_arm": 1,
        "correct_raw_actions_must_differ": True,
        "correct_raw_action_sets_disjoint": True,
        "action_universe_size": RAW_ACTION_COUNT,
    }
    if dict(strict) != expected_strict:
        raise PrimaryBenchmarkError("strict-pair contract drifted")
    if (
        masking.get("arms") != list(ARMS)
        or masking.get("only_provider_visible_pair_difference")
        != "rendered context fragment"
        or masking.get("pair_shared_raw_action_order") is not True
        or masking.get("pair_shared_opaque_option_ids") is not True
        or masking.get("answer_example_in_prompt") is not False
    ):
        raise PrimaryBenchmarkError("masking contract drifted")
    if (
        schedule.get("action_order_namespace")
        != "spark-strong-k4-utilization-primary-benchmark-v1:base-option-permutation"
        or schedule.get("schedule_vector_frozen_before_private_shard_read") is not True
        or schedule.get("schedule_may_not_be_changed_after_model_output") is not True
    ):
        raise PrimaryBenchmarkError("schedule contract drifted")
    if dict(route) != EXPECTED_PRIMARY_ROUTE:
        raise PrimaryBenchmarkError("prospective-primary route binding drifted")
    if dict(analysis) != EXPECTED_ANALYSIS_BINDING:
        raise PrimaryBenchmarkError("primary analysis binding drifted")
    if dict(baselines) != EXPECTED_TARGET_BLIND_BASELINES:
        raise PrimaryBenchmarkError("target-blind baseline contract drifted")
    if (
        barrier.get("private_shards_read_before_reviewed_plan") is not False
        or barrier.get("reviewed_plan_semantic_and_file_sha256_required") is not True
        or barrier.get("model_outputs_read") is not False
        or barrier.get("provider_calls_made") != 0
        or artifact.get("exact_48_task_bijection_required") is not True
        or artifact.get("exclusive_create_no_overwrite") is not True
        or artifact.get("output_mode") != "0600"
        or artifact.get("provider_calls_made") != 0
        or artifact.get("model_outputs_read") is not False
        or artifact.get("final_benchmark_minted_only_after_all_128_shards_validate")
        is not True
        or dict(live) != EXPECTED_REMAINING_LIVE_BARRIERS
    ):
        raise PrimaryBenchmarkError("construction or live barrier drifted")


def action_order_for_pair(pair_ordinal: int, namespace: str) -> tuple[int, ...]:
    """Return the frozen cyclic Latin action order for one of 24 pair slots."""

    if type(pair_ordinal) is not int or not 0 <= pair_ordinal < PAIR_COUNT:
        raise PrimaryBenchmarkError("pair ordinal is outside the 24-pair schedule")
    if not isinstance(namespace, str) or not namespace:
        raise PrimaryBenchmarkError("action-order namespace is malformed")
    base = tuple(
        sorted(
            range(RAW_ACTION_COUNT),
            key=lambda raw: hashlib.sha256(f"{namespace}:{raw}".encode("ascii")).digest(),
        )
    )
    offset = pair_ordinal % RAW_ACTION_COUNT
    return base[offset:] + base[:offset]


def _pair_slots(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    namespace = str(config["schedule"]["action_order_namespace"])
    slots: list[dict[str, Any]] = []
    ordinal = 0
    for stratum in config["cohort_selection"]["construction_strata_in_frozen_order"]:
        for rank in range(TARGET_PER_STRATUM):
            first, second = (
                ARMS if rank % 2 == 0 else tuple(reversed(ARMS))
            )
            slots.append(
                {
                    "pair_ordinal": ordinal,
                    "construction_stratum": stratum,
                    "within_stratum_rank": rank,
                    "phase_1_arm": first,
                    "phase_2_arm": second,
                    "action_order": list(action_order_for_pair(ordinal, namespace)),
                }
            )
            ordinal += 1
    return slots


def _plan_upstream_bindings(config: Mapping[str, Any]) -> dict[str, Any]:
    geometry = config["upstream_geometry"]
    power = config["upstream_power_gate"]
    return {
        "artifact_manifest_file_sha256": geometry["artifact_manifest_file_sha256"],
        "artifact_manifest_sha256": geometry["artifact_manifest_sha256"],
        "feasibility_config_file_sha256": geometry["feasibility_config_file_sha256"],
        "feasibility_plan_file_sha256": geometry["feasibility_plan_file_sha256"],
        "feasibility_plan_sha256": geometry["feasibility_plan_sha256"],
        "feasibility_scan_sha256": geometry["feasibility_scan_sha256"],
        "shard_files_manifest_sha256": geometry["shard_files_manifest_sha256"],
        "power_config_file_sha256": power["config_file_sha256"],
        "power_plan_file_sha256": power["plan_file_sha256"],
        "power_plan_sha256": power["plan_sha256"],
        "power_result_file_sha256": power["result_file_sha256"],
        "power_result_sha256": power["result_sha256"],
    }


def _plan_cohort_contract() -> dict[str, Any]:
    return {
        "tier_id": feasibility.STRICT_TIER,
        "target_per_stratum": TARGET_PER_STRATUM,
        "fallback_per_stratum": TARGET_PER_STRATUM,
        "world_count": PAIR_COUNT,
        "task_count": TASK_COUNT,
        "strata": list(spark_lineage.MOTIF_STRATA),
        "world_capacity": 1,
    }


def _load_tracked_upstreams(config: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """Read and validate only tracked configs/plans/results and the safe manifest."""

    validate_config(config)
    geometry = config["upstream_geometry"]
    power_binding = config["upstream_power_gate"]
    supersession = config["supersession"]

    def exact(
        section: Mapping[str, Any], path_key: str, sha_key: str, label: str
    ) -> dict[str, Any]:
        path = _project_path(root, section[path_key], label)
        value, payload = _read_json(path, label)
        if _sha256_bytes(payload) != section[sha_key]:
            raise PrimaryBenchmarkError(f"{label} raw bytes differ from v2 binding")
        return value

    superseded = exact(
        supersession,
        "superseded_config_relative_path",
        "superseded_config_file_sha256",
        "superseded v1 config",
    )
    if superseded.get("protocol_id") != "spark-strong-k4-utilization-primary-benchmark-v1":
        raise PrimaryBenchmarkError("superseded config identity drifted")

    manifest = exact(
        geometry,
        "artifact_manifest_relative_path",
        "artifact_manifest_file_sha256",
        "safe feasibility manifest",
    )
    manifest_sha = _check_inner_digest(manifest, "manifest_sha256", "safe manifest")
    if (
        manifest_sha != geometry["artifact_manifest_sha256"]
        or manifest.get("protocol_id") != feasibility.PROTOCOL_ID
        or manifest.get("status") != "complete_outcome_conditioned_development_scan"
    ):
        raise PrimaryBenchmarkError("safe manifest identity drifted")
    safety = _require_mapping(manifest.get("safety"), "safe manifest safety")
    if (
        safety.get("development_only") is not True
        or safety.get("confirmatory") is not False
        or safety.get("model_outputs_read") is not False
        or safety.get("provider_calls_made") != 0
    ):
        raise PrimaryBenchmarkError("safe manifest development boundary drifted")

    feasibility_config = exact(
        geometry,
        "feasibility_config_relative_path",
        "feasibility_config_file_sha256",
        "feasibility config",
    )
    feasibility_plan = exact(
        geometry,
        "feasibility_plan_relative_path",
        "feasibility_plan_file_sha256",
        "feasibility plan",
    )
    plan_sha = _check_inner_digest(feasibility_plan, "plan_sha256", "feasibility plan")
    if (
        feasibility_config.get("protocol_id") != feasibility.PROTOCOL_ID
        or feasibility_config.get("kind") != feasibility.CONFIG_KIND
        or feasibility_plan.get("protocol_id") != feasibility.PROTOCOL_ID
        or feasibility_plan.get("kind") != feasibility.PLAN_KIND
        or plan_sha != geometry["feasibility_plan_sha256"]
        or feasibility_plan.get("config_file_sha256")
        != geometry["feasibility_config_file_sha256"]
        or feasibility_plan.get("development_only") is not True
        or feasibility_plan.get("model_outputs_read") is not False
        or feasibility_plan.get("provider_calls_made") != 0
    ):
        raise PrimaryBenchmarkError("feasibility config/plan binding drifted")
    candidates = feasibility_plan.get("candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != geometry["world_count"]
        or [row.get("candidate_index") for row in candidates if isinstance(row, Mapping)]
        != list(range(geometry["world_count"]))
    ):
        raise PrimaryBenchmarkError("feasibility plan candidate vector drifted")

    power_config = exact(
        power_binding, "config_relative_path", "config_file_sha256", "power config"
    )
    power_plan = exact(
        power_binding, "plan_relative_path", "plan_file_sha256", "power plan"
    )
    power_result = exact(
        power_binding, "result_relative_path", "result_file_sha256", "power result"
    )
    if (
        power_config.get("protocol_id") != power_binding["protocol_id"]
        or _check_inner_digest(power_plan, "plan_sha256", "power plan")
        != power_binding["plan_sha256"]
        or _check_inner_digest(power_result, "result_sha256", "power result")
        != power_binding["result_sha256"]
        or power_result.get("classification", {}).get("overall")
        != power_binding["required_historical_overall_classification"]
        or power_result.get("tier_result", {}).get("classification")
        != power_binding["required_tier_classification"]
        or power_result.get("model_outputs_read") is not False
        or power_result.get("provider_calls_made") != 0
    ):
        raise PrimaryBenchmarkError("prospective power result binding drifted")

    artifacts = _require_mapping(manifest.get("artifacts"), "safe manifest artifacts")
    shard_meta = _require_mapping(artifacts.get("shards_private"), "shard metadata")
    files = shard_meta.get("files")
    if (
        not isinstance(files, list)
        or len(files) != geometry["shard_count"]
        or shard_meta.get("count") != geometry["shard_count"]
        or shard_meta.get("total_size_bytes") != geometry["shard_total_size_bytes"]
        or shard_meta.get("files_manifest_sha256")
        != geometry["shard_files_manifest_sha256"]
        or _sha256_json(files) != geometry["shard_files_manifest_sha256"]
    ):
        raise PrimaryBenchmarkError("private shard metadata binding drifted")
    bindings = _require_mapping(manifest.get("bindings"), "safe manifest bindings")
    if (
        bindings.get("config_file_sha256") != geometry["feasibility_config_file_sha256"]
        or bindings.get("plan_sha256") != geometry["feasibility_plan_sha256"]
        or bindings.get("scan_sha256") != geometry["feasibility_scan_sha256"]
    ):
        raise PrimaryBenchmarkError("safe-manifest scan bindings drifted")
    return {
        "safe_manifest": manifest,
        "feasibility_config": feasibility_config,
        "feasibility_plan": feasibility_plan,
        "power_config": power_config,
        "power_plan": power_plan,
        "power_result": power_result,
    }


def build_construction_plan(
    config: Mapping[str, Any],
    *,
    config_file_sha256: str,
    source_manifest_sha256: str,
    project_root: str | Path = PROJECT_ROOT,
    source_freeze_git_head: str | None = None,
) -> dict[str, Any]:
    """Build the target-free plan.  This function never opens a private shard."""

    validate_config(config)
    config_sha = _require_sha256(config_file_sha256, "config file")
    source_sha = _require_sha256(source_manifest_sha256, "source manifest")
    source_head = _require_git_commit(source_freeze_git_head, "source-freeze Git head")
    upstreams = _load_tracked_upstreams(config, Path(project_root))
    unsigned: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": config["evidence_scope"],
        "world_layer_label": WORLD_LAYER_LABEL,
        "model_response_layer_label": MODEL_RESPONSE_LAYER_LABEL,
        "config_file_sha256": config_sha,
        "source_manifest_sha256": source_sha,
        "source_freeze_git_head": source_head,
        "upstream_bindings": _plan_upstream_bindings(config),
        "cohort_contract": _plan_cohort_contract(),
        "primary_route": dict(config["primary_route"]),
        "pair_slots": _pair_slots(config),
        "private_shards_read": False,
        "target_or_pair_identity_read": False,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "final_benchmark_minted": False,
    }
    # Keep the read visible in the contract: only safe tracked inputs were opened.
    del upstreams
    return {**unsigned, "plan_sha256": _sha256_json(unsigned)}


def validate_construction_plan(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    config_file_sha256: str,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> None:
    validate_config(config)
    if not isinstance(plan, Mapping):
        raise PrimaryBenchmarkError("construction plan must be an object")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    expected_keys = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence_scope",
        "world_layer_label",
        "model_response_layer_label",
        "config_file_sha256",
        "source_manifest_sha256",
        "source_freeze_git_head",
        "upstream_bindings",
        "cohort_contract",
        "primary_route",
        "pair_slots",
        "private_shards_read",
        "target_or_pair_identity_read",
        "model_outputs_read",
        "provider_calls_made",
        "final_benchmark_minted",
        "plan_sha256",
    }
    if (
        set(plan) != expected_keys
        or plan.get("schema_version") != ARTIFACT_SCHEMA_VERSION
        or plan.get("kind") != PLAN_KIND
        or plan.get("protocol_id") != PROTOCOL_ID
        or plan.get("evidence_scope") != config["evidence_scope"]
        or plan.get("world_layer_label") != WORLD_LAYER_LABEL
        or plan.get("model_response_layer_label") != MODEL_RESPONSE_LAYER_LABEL
        or plan.get("config_file_sha256") != config_file_sha256
        or not _is_sha256(plan.get("source_manifest_sha256"))
        or plan.get("upstream_bindings") != _plan_upstream_bindings(config)
        or plan.get("cohort_contract") != _plan_cohort_contract()
        or plan.get("primary_route") != dict(config["primary_route"])
        or plan.get("plan_sha256") != _sha256_json(unsigned)
        or plan.get("pair_slots") != _pair_slots(config)
        or plan.get("private_shards_read") is not False
        or plan.get("target_or_pair_identity_read") is not False
        or plan.get("model_outputs_read") is not False
        or plan.get("provider_calls_made") != 0
        or plan.get("final_benchmark_minted") is not False
    ):
        raise PrimaryBenchmarkError("construction plan identity or schedule drifted")
    _require_git_commit(plan.get("source_freeze_git_head"), "plan source-freeze Git head")
    _load_tracked_upstreams(config, Path(project_root))
    if require_current_source:
        current = source_manifest(project_root)
        if current.get("source_manifest_sha256") != plan.get("source_manifest_sha256"):
            raise PrimaryBenchmarkError("construction source manifest drifted")
        _, source_pathspecs = _assert_clean_source_freeze(Path(project_root), current)
        _assert_frozen_commit_matches_source(
            Path(project_root),
            str(plan["source_freeze_git_head"]),
            source_pathspecs,
        )


def _verify_reviewed_plan_file(
    plan: Mapping[str, Any],
    *,
    reviewed_plan_sha256: str,
    reviewed_plan_file_sha256: str,
    plan_path: str | Path,
) -> None:
    if reviewed_plan_sha256 != plan.get("plan_sha256"):
        raise PrimaryBenchmarkError("reviewed semantic plan SHA-256 differs")
    expected_file = _require_sha256(reviewed_plan_file_sha256, "reviewed plan file")
    loaded, payload = _read_json(Path(plan_path), "reviewed construction plan")
    if _sha256_bytes(payload) != expected_file:
        raise PrimaryBenchmarkError("reviewed plan file SHA-256 differs")
    if _canonical_json_bytes(loaded) != _canonical_json_bytes(plan):
        raise PrimaryBenchmarkError("reviewed plan file differs from supplied plan")


def _stream_validated_strict_worlds(
    config: Mapping[str, Any],
    safe_manifest: Mapping[str, Any],
    feasibility_config: Mapping[str, Any],
    feasibility_plan: Mapping[str, Any],
    *,
    project_root: Path,
    shard_validator: Callable[..., tuple[int, int, list[Mapping[str, Any]]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate one private shard at a time and retain strict eligibility only."""

    geometry = config["upstream_geometry"]
    shard_meta = safe_manifest["artifacts"]["shards_private"]
    files = shard_meta["files"]
    cursor = 0
    total_bytes = 0
    compact: list[dict[str, Any]] = []
    strict_pair_count = 0
    eligible_world_count = 0
    for ordinal, metadata in enumerate(files):
        if not isinstance(metadata, Mapping):
            raise PrimaryBenchmarkError("private shard metadata row is malformed")
        expected_range = {
            "start": ordinal * geometry["shard_world_count"],
            "count": geometry["shard_world_count"],
            "end_exclusive": (ordinal + 1) * geometry["shard_world_count"],
        }
        if metadata.get("candidate_range") != expected_range:
            raise PrimaryBenchmarkError("private shard metadata range drifted")
        path = _project_path(project_root, metadata.get("relative_path"), "private shard")
        shard, payload = _read_json(path, f"private shard {ordinal:03d}")
        if (
            len(payload) != metadata.get("size_bytes")
            or _sha256_bytes(payload) != metadata.get("file_sha256")
            or shard.get("shard_sha256") != metadata.get("shard_sha256")
        ):
            raise PrimaryBenchmarkError(f"private shard {ordinal:03d} bytes differ")
        try:
            start, end, worlds = shard_validator(
                shard,
                config_file_sha256=geometry["feasibility_config_file_sha256"],
                plan=feasibility_plan,
                config=feasibility_config,
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise PrimaryBenchmarkError(
                f"private shard {ordinal:03d} schema validation failed"
            ) from exc
        if (start, end) != (expected_range["start"], expected_range["end_exclusive"]):
            raise PrimaryBenchmarkError("validated private shard range drifted")
        for world in worlds:
            index = world.get("candidate_index")
            pairs_by_tier = world.get("pair_candidates")
            parent_hash = world.get("parent_canonical_hash")
            if (
                type(index) is not int
                or not isinstance(pairs_by_tier, Mapping)
                or not _is_sha256(parent_hash)
            ):
                raise PrimaryBenchmarkError("validated world eligibility is malformed")
            strict_rows = pairs_by_tier.get(feasibility.STRICT_TIER)
            if not isinstance(strict_rows, list):
                raise PrimaryBenchmarkError("validated strict eligibility is malformed")
            copied = [dict(row) for row in strict_rows if isinstance(row, Mapping)]
            if len(copied) != len(strict_rows):
                raise PrimaryBenchmarkError("strict pair row is malformed")
            compact.append(
                {
                    "candidate_index": index,
                    "parent_canonical_hash": parent_hash,
                    "pair_candidates": {feasibility.STRICT_TIER: copied},
                }
            )
            strict_pair_count += len(copied)
            eligible_world_count += bool(copied)
        cursor = end
        total_bytes += len(payload)
    if (
        cursor != geometry["world_count"]
        or len(compact) != geometry["world_count"]
        or [row["candidate_index"] for row in compact] != list(range(geometry["world_count"]))
        or total_bytes != geometry["shard_total_size_bytes"]
    ):
        raise PrimaryBenchmarkError("private shard stream is incomplete")
    return compact, {
        "validated_shard_count": len(files),
        "validated_world_count": len(compact),
        "validated_total_size_bytes": total_bytes,
        "shard_files_manifest_sha256": geometry["shard_files_manifest_sha256"],
        "strict_eligible_world_count": eligible_world_count,
        "strict_pair_candidate_count": strict_pair_count,
        "all_shards_hash_and_schema_validated": True,
    }


def _opaque_token(prefix: str, payload: object) -> str:
    digest = hashlib.sha256(
        f"{PROTOCOL_ID}:{prefix}:".encode("ascii") + _canonical_json_bytes(payload)
    ).digest()
    token = base64.b32encode(digest).decode("ascii").rstrip("=")[:14]
    return f"{prefix}-{token}"


def _option_ids(pair_anchor_sha256: str) -> tuple[str, ...]:
    _require_sha256(pair_anchor_sha256, "pair anchor")
    result = tuple(
        "Q"
        + _sha256_json(
            {
                "protocol_id": PROTOCOL_ID,
                "pair_anchor_sha256": pair_anchor_sha256,
                "display_position": position,
            }
        )[:8].upper()
        for position in range(RAW_ACTION_COUNT)
    )
    if len(set(result)) != RAW_ACTION_COUNT:
        raise PrimaryBenchmarkError("opaque option-id collision")
    return result


def _target_free_prompt_context(
    world_seed: int, expected_parent_hash: str | None = None
) -> tuple[Any, dict[str, Any]]:
    world = prompt_support._target_free_support_world(world_seed)
    parent = spark_lineage.select_parent(world)
    parent_hash = dsl.canonical_hash(parent)
    if expected_parent_hash is not None and parent_hash != expected_parent_hash:
        raise PrimaryBenchmarkError("target-free parent replay differs from shard binding")
    context = {
        "D0": [
            {"point": list(example.point), "label": example.label}
            for example in world.train
        ],
        "parent": dsl.to_sexpr(parent),
        "old_subtrees": {
            "LEFT": dsl.to_sexpr(spark_lineage.get_subtree(parent, (1, 1))),
            "RIGHT": dsl.to_sexpr(spark_lineage.get_subtree(parent, (1, 2))),
        },
    }
    return world, context


def _motif_catalog() -> dict[str, Mapping[str, Any]]:
    return {
        str(row["motif_id"]): row
        for row in feasibility.enumerate_full_motif_library()
    }


def _public_action_features(
    world: Any,
    motif_id: str,
    lineages: Mapping[Any, Any] | None = None,
) -> list[dict[str, Any]]:
    if lineages is None:
        lineages = strong_scan._lineage_index(
            spark_lineage.enumerate_reachable_children(world)
        )
    rows: list[dict[str, Any]] = []
    for raw, action in enumerate(strong_scan._raw_actions(world, motif_id)):
        rows.append(
            {
                "raw_action_index": raw,
                "public_features": prompt_support._action_public_features(
                    world, lineages.get(action)
                ),
            }
        )
    return rows


def _validate_selected_pair(pair: Mapping[str, Any], stratum: str) -> None:
    left = pair.get("context_a_correct_raw_action_indices")
    right = pair.get("context_b_correct_raw_action_indices")
    if (
        pair.get("tier_id") != feasibility.STRICT_TIER
        or pair.get("stratum") != stratum
        or not isinstance(left, list)
        or not isinstance(right, list)
        or len(left) != 1
        or len(right) != 1
        or type(left[0]) is not int
        or type(right[0]) is not int
        or not 0 <= left[0] < RAW_ACTION_COUNT
        or not 0 <= right[0] < RAW_ACTION_COUNT
        or left[0] == right[0]
        or pair.get("correct_raw_action_sets_disjoint") is not True
        or pair.get("context_a_motif_id") == pair.get("context_b_motif_id")
    ):
        raise PrimaryBenchmarkError("selected pair is not strict unique-action geometry")


def _build_baseline_report(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    policies = (
        tuple(f"fixed-semantic-{raw:02d}" for raw in range(RAW_ACTION_COUNT))
        + tuple(f"fixed-display-position-{position:02d}" for position in range(RAW_ACTION_COUNT))
        + tuple(prompt_support.PUBLIC_K1_POLICY_IDS)
    )
    reports: list[dict[str, Any]] = []
    for policy_id in policies:
        counts = Counter({"favorable": 0, "adverse": 0, "tie": 0, "complete_switch": 0})
        signed_total = 0
        for pair in pairs:
            arms = pair["arms"]
            selected = {
                arm: prompt_support._select_baseline_raw_action(
                    policy_id,
                    arms[arm]["public_action_features"],
                    pair["action_order"],
                )
                for arm in ARMS
            }
            correct = {
                arm: int(arms[arm]["correct_raw_action_indices"][0]) for arm in ARMS
            }
            own = sum(selected[arm] == correct[arm] for arm in ARMS)
            cross = (
                int(selected["context_a"] == correct["context_b"])
                + int(selected["context_b"] == correct["context_a"])
            )
            signed = own - cross
            signed_total += signed
            counts["favorable" if signed > 0 else "adverse" if signed < 0 else "tie"] += 1
            counts["complete_switch"] += own == 2
        reports.append(
            {
                "policy_id": policy_id,
                "favorable_world_count": counts["favorable"],
                "adverse_world_count": counts["adverse"],
                "tie_world_count": counts["tie"],
                "complete_switch_world_count": counts["complete_switch"],
                "signed_total": signed_total,
            }
        )
    return {
        "inferential_role": "descriptive_shortcut_sensitivity_only",
        "B_star_or_posthoc_policy_selection": False,
        "policy_count": len(reports),
        "policies": reports,
    }


def _task_record(task_id: str, prompt: str) -> dict[str, str]:
    return {
        "task_id": task_id,
        "rendered_prompt": prompt,
        "prompt_sha256": _sha256_text(prompt),
    }


def _build_masked_materials(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    matching: Mapping[str, Any],
    feasibility_plan: Mapping[str, Any],
    shard_audit: Mapping[str, Any],
    parent_hash_by_candidate: Mapping[int, str],
    *,
    reviewed_plan_file_sha256: str,
    public_feature_builder: Callable[
        [Any, str, Mapping[Any, Any]], list[dict[str, Any]]
    ] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    assignments = matching.get("assignments")
    if (
        matching.get("complete") is not True
        or matching.get("selected_q") != TARGET_PER_STRATUM
        or not isinstance(assignments, list)
        or len(assignments) != PAIR_COUNT
    ):
        raise PrimaryBenchmarkError("strict q6 matching is not complete")
    candidates = feasibility_plan["candidates"]
    by_assignment = {
        (row["construction_stratum"], int(row["candidate_index"])): row
        for row in assignments
    }
    ordered: list[Mapping[str, Any]] = []
    for stratum in spark_lineage.MOTIF_STRATA:
        rows = sorted(
            (
                row for (row_stratum, _), row in by_assignment.items()
                if row_stratum == stratum
            ),
            key=lambda row: int(row["candidate_index"]),
        )
        if len(rows) != TARGET_PER_STRATUM:
            raise PrimaryBenchmarkError("matching is not exactly q6 in every stratum")
        ordered.extend(rows)

    catalog = _motif_catalog()
    feature_builder = public_feature_builder or (
        lambda world, motif_id, lineages: _public_action_features(
            world, motif_id, lineages
        )
    )
    task_records: dict[str, dict[str, str]] = {}
    private_pairs: list[dict[str, Any]] = []
    for slot, assignment in zip(plan["pair_slots"], ordered, strict=True):
        ordinal = int(slot["pair_ordinal"])
        candidate_index = int(assignment["candidate_index"])
        if assignment["construction_stratum"] != slot["construction_stratum"]:
            raise PrimaryBenchmarkError("matching assignment differs from frozen pair slot")
        pair = assignment.get("pair")
        if not isinstance(pair, Mapping):
            raise PrimaryBenchmarkError("matching assignment lacks its strict pair")
        _validate_selected_pair(pair, str(slot["construction_stratum"]))
        seed = candidates[candidate_index].get("world_seed")
        if type(seed) is not int:
            raise PrimaryBenchmarkError("selected feasibility seed is malformed")
        expected_parent_hash = parent_hash_by_candidate.get(candidate_index)
        if not _is_sha256(expected_parent_hash):
            raise PrimaryBenchmarkError("selected world lost its target-free parent binding")
        world, prompt_context = _target_free_prompt_context(seed, expected_parent_hash)
        target_free_lineages = (
            strong_scan._lineage_index(spark_lineage.enumerate_reachable_children(world))
            if public_feature_builder is None
            else {}
        )
        pair_anchor = _sha256_json(
            {
                "protocol_id": PROTOCOL_ID,
                "plan_sha256": plan["plan_sha256"],
                "candidate_index": candidate_index,
                "construction_stratum": slot["construction_stratum"],
                "context_a_motif_id": pair["context_a_motif_id"],
                "context_b_motif_id": pair["context_b_motif_id"],
            }
        )
        option_ids = _option_ids(pair_anchor)
        action_order = list(slot["action_order"])
        option_to_raw = {
            option_ids[position]: raw for position, raw in enumerate(action_order)
        }
        raw_to_option = {raw: option for option, raw in option_to_raw.items()}
        task_ids = {
            arm: _opaque_token(
                "TASK", {"pair_anchor_sha256": pair_anchor, "arm": arm}
            )
            for arm in ARMS
        }
        arms: dict[str, Any] = {}
        for arm in ARMS:
            motif_id = str(pair[f"{arm}_motif_id"])
            motif = catalog.get(motif_id)
            if motif is None:
                raise PrimaryBenchmarkError("selected motif is absent from frozen catalog")
            correct = list(pair[f"{arm}_correct_raw_action_indices"])
            correct_actions = pair.get(f"{arm}_correct_actions")
            if not isinstance(correct_actions, list) or len(correct_actions) != 1:
                raise PrimaryBenchmarkError("strict pair lacks one correct-action commitment")
            prompt = prompt_support.render_fair_choice_prompt(
                prompt_context,
                str(motif["motif_sexpr"]),
                action_order,
                option_ids,
            )
            task_records[task_ids[arm]] = _task_record(task_ids[arm], prompt)
            arms[arm] = {
                "task_id": task_ids[arm],
                "motif": dict(motif),
                "correct_raw_action_indices": correct,
                "correct_option_ids": [raw_to_option[int(correct[0])]],
                "correct_action_commitment": dict(correct_actions[0]),
                "public_action_features": feature_builder(
                    world, motif_id, target_free_lineages
                ),
            }
        private_pairs.append(
            {
                "pair_ordinal": ordinal,
                "pair_id": _opaque_token("PAIR", {"pair_anchor_sha256": pair_anchor}),
                "pair_anchor_sha256": pair_anchor,
                "construction_stratum": slot["construction_stratum"],
                "within_stratum_rank": slot["within_stratum_rank"],
                "condition_order": [slot["phase_1_arm"], slot["phase_2_arm"]],
                "world_binding": {
                    "candidate_index": candidate_index,
                    "world_seed": seed,
                    "parent_canonical_hash": expected_parent_hash,
                    "development_only": True,
                },
                "prompt_context": prompt_context,
                "action_order": action_order,
                "option_ids_by_display_position": list(option_ids),
                "option_to_raw_action": option_to_raw,
                "K2_opportunity_count_each_arm": pair.get("k2_opportunity_count"),
                "arms": arms,
            }
        )

    public_tasks = [
        task_records[pair["arms"][pair["condition_order"][phase]]["task_id"]]
        for phase in range(2)
        for pair in private_pairs
    ]
    baseline_report = _build_baseline_report(private_pairs)
    design_commitment = _sha256_json(
        {
            "protocol_id": PROTOCOL_ID,
            "config_file_sha256": plan["config_file_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "pairs": private_pairs,
            "baseline_report": baseline_report,
        }
    )
    public_unsigned = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": PUBLIC_MANIFEST_KIND,
        "protocol_id": PROTOCOL_ID,
        "world_layer_label": WORLD_LAYER_LABEL,
        "model_response_layer_label": MODEL_RESPONSE_LAYER_LABEL,
        "task_count": TASK_COUNT,
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "config_file_sha256": plan["config_file_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "private_design_commitment_sha256": design_commitment,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "tasks": public_tasks,
    }
    public = {
        **public_unsigned,
        "public_manifest_sha256": _sha256_json(public_unsigned),
    }
    private_unsigned = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": PRIVATE_KEY_KIND,
        "protocol_id": PROTOCOL_ID,
        "world_layer_label": WORLD_LAYER_LABEL,
        "model_response_layer_label": MODEL_RESPONSE_LAYER_LABEL,
        "pair_count": PAIR_COUNT,
        "task_count": TASK_COUNT,
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "config_file_sha256": plan["config_file_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "reviewed_plan_file_sha256": reviewed_plan_file_sha256,
        "public_manifest_sha256": public["public_manifest_sha256"],
        "private_design_commitment_sha256": design_commitment,
        "upstream_scan_sha256": config["upstream_geometry"]["feasibility_scan_sha256"],
        "primary_route": dict(config["primary_route"]),
        "analysis_binding": dict(config["analysis_binding"]),
        "pairs": private_pairs,
        "baseline_report": baseline_report,
        "model_outputs_read": False,
        "provider_calls_made": 0,
    }
    private = {
        **private_unsigned,
        "private_key_sha256": _sha256_json(private_unsigned),
    }
    public_file_sha = _sha256_bytes(_rendered_json_bytes(public))
    private_file_sha = _sha256_bytes(_rendered_json_bytes(private))
    selected_indices = [
        int(pair["world_binding"]["candidate_index"]) for pair in private_pairs
    ]
    result_unsigned = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": CONSTRUCTION_RESULT_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence": False,
        "world_layer_label": WORLD_LAYER_LABEL,
        "model_response_layer_label": MODEL_RESPONSE_LAYER_LABEL,
        "independent_heldout_confirmation": False,
        "classification": "strict_q6_masked_benchmark_construction_complete",
        "config_file_sha256": plan["config_file_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "reviewed_plan_sha256": plan["plan_sha256"],
        "reviewed_plan_file_sha256": reviewed_plan_file_sha256,
        "shard_validation": dict(shard_audit),
        "selection": {
            "tier_id": feasibility.STRICT_TIER,
            "selected_q": TARGET_PER_STRATUM,
            "world_count": PAIR_COUNT,
            "task_count": TASK_COUNT,
            "selected_candidate_indices_in_pair_slot_order": selected_indices,
            "counts_by_construction_stratum": {
                stratum: sum(pair["construction_stratum"] == stratum for pair in private_pairs)
                for stratum in spark_lineage.MOTIF_STRATA
            },
        },
        "artifacts": {
            "public_manifest_sha256": public["public_manifest_sha256"],
            "public_manifest_file_sha256": public_file_sha,
            "private_key_sha256": private["private_key_sha256"],
            "private_key_file_sha256": private_file_sha,
            "private_design_commitment_sha256": design_commitment,
        },
        "final_benchmark_minted": True,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "provider_calls_authorized": False,
    }
    result = {
        **result_unsigned,
        "construction_result_sha256": _sha256_json(result_unsigned),
    }
    validate_public_manifest(public)
    validate_private_key(private, public, config=config, plan=plan)
    validate_construction_result(result, public, private)
    return public, private, result


def construct_benchmark(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    config_file_sha256: str,
    reviewed_plan_sha256: str,
    reviewed_plan_file_sha256: str,
    plan_path: str | Path,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
    shard_validator: Callable[..., tuple[int, int, list[Mapping[str, Any]]]] | None = None,
    public_feature_builder: Callable[
        [Any, str, Mapping[Any, Any]], list[dict[str, Any]]
    ] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate all 128 private shards and construct the masked benchmark."""

    root = Path(project_root)
    validate_construction_plan(
        config,
        plan,
        config_file_sha256=config_file_sha256,
        project_root=root,
        require_current_source=require_current_source,
    )
    # This barrier is deliberately before loading any shard path or bytes.
    _verify_reviewed_plan_file(
        plan,
        reviewed_plan_sha256=reviewed_plan_sha256,
        reviewed_plan_file_sha256=reviewed_plan_file_sha256,
        plan_path=plan_path,
    )
    upstreams = _load_tracked_upstreams(config, root)
    validator = shard_validator or feasibility._validate_shard
    compact, shard_audit = _stream_validated_strict_worlds(
        config,
        upstreams["safe_manifest"],
        upstreams["feasibility_config"],
        upstreams["feasibility_plan"],
        project_root=root,
        shard_validator=validator,
    )
    matching = feasibility.deterministic_tier_matching(
        compact,
        tier_id=feasibility.STRICT_TIER,
        strata=tuple(config["cohort_selection"]["construction_strata_in_frozen_order"]),
        target_per_stratum=TARGET_PER_STRATUM,
        fallback_per_stratum=TARGET_PER_STRATUM,
    )
    return _build_masked_materials(
        config,
        plan,
        matching,
        upstreams["feasibility_plan"],
        shard_audit,
        {
            int(world["candidate_index"]): str(world["parent_canonical_hash"])
            for world in compact
        },
        reviewed_plan_file_sha256=reviewed_plan_file_sha256,
        public_feature_builder=public_feature_builder,
    )


def validate_public_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise PrimaryBenchmarkError("public manifest must be an object")
    unsigned = {
        key: value for key, value in manifest.items() if key != "public_manifest_sha256"
    }
    tasks = manifest.get("tasks")
    expected_keys = {
        "schema_version",
        "kind",
        "protocol_id",
        "world_layer_label",
        "model_response_layer_label",
        "task_count",
        "source_manifest_sha256",
        "config_file_sha256",
        "plan_sha256",
        "private_design_commitment_sha256",
        "model_outputs_read",
        "provider_calls_made",
        "tasks",
        "public_manifest_sha256",
    }
    if (
        set(manifest) != expected_keys
        or manifest.get("schema_version") != ARTIFACT_SCHEMA_VERSION
        or manifest.get("kind") != PUBLIC_MANIFEST_KIND
        or manifest.get("protocol_id") != PROTOCOL_ID
        or manifest.get("world_layer_label") != WORLD_LAYER_LABEL
        or manifest.get("model_response_layer_label") != MODEL_RESPONSE_LAYER_LABEL
        or manifest.get("task_count") != TASK_COUNT
        or manifest.get("model_outputs_read") is not False
        or manifest.get("provider_calls_made") != 0
        or not _is_sha256(manifest.get("private_design_commitment_sha256"))
        or manifest.get("public_manifest_sha256") != _sha256_json(unsigned)
        or not isinstance(tasks, list)
        or len(tasks) != TASK_COUNT
    ):
        raise PrimaryBenchmarkError("public manifest identity or count drifted")
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, Mapping) or set(task) != {
            "task_id", "rendered_prompt", "prompt_sha256"
        }:
            raise PrimaryBenchmarkError("public task schema drifted")
        task_id = task["task_id"]
        prompt = task["rendered_prompt"]
        if (
            not isinstance(task_id, str)
            or _TASK_ID_RE.fullmatch(task_id) is None
            or task_id in seen
            or not isinstance(prompt, str)
            or task["prompt_sha256"] != _sha256_text(prompt)
        ):
            raise PrimaryBenchmarkError("public task identity drifted")
        prompt_support._validate_rendered_prompt(prompt)
        seen.add(task_id)


def validate_private_key(
    private: Mapping[str, Any],
    public: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    target_free_context_builder: Callable[
        [int, str | None], tuple[Any, dict[str, Any]]
    ]
    | None = None,
) -> None:
    validate_config(config)
    validate_public_manifest(public)
    unsigned = {key: value for key, value in private.items() if key != "private_key_sha256"}
    pairs = private.get("pairs")
    if (
        private.get("schema_version") != ARTIFACT_SCHEMA_VERSION
        or private.get("kind") != PRIVATE_KEY_KIND
        or private.get("protocol_id") != PROTOCOL_ID
        or private.get("world_layer_label") != WORLD_LAYER_LABEL
        or private.get("model_response_layer_label") != MODEL_RESPONSE_LAYER_LABEL
        or private.get("pair_count") != PAIR_COUNT
        or private.get("task_count") != TASK_COUNT
        or private.get("source_manifest_sha256") != plan.get("source_manifest_sha256")
        or private.get("config_file_sha256") != plan.get("config_file_sha256")
        or private.get("plan_sha256") != plan.get("plan_sha256")
        or private.get("upstream_scan_sha256")
        != config["upstream_geometry"]["feasibility_scan_sha256"]
        or private.get("primary_route") != dict(config["primary_route"])
        or private.get("analysis_binding") != dict(config["analysis_binding"])
        or public.get("source_manifest_sha256") != plan.get("source_manifest_sha256")
        or public.get("config_file_sha256") != plan.get("config_file_sha256")
        or public.get("plan_sha256") != plan.get("plan_sha256")
        or private.get("public_manifest_sha256") != public.get("public_manifest_sha256")
        or not _is_sha256(private.get("reviewed_plan_file_sha256"))
        or private.get("private_design_commitment_sha256")
        != public.get("private_design_commitment_sha256")
        or private.get("private_key_sha256") != _sha256_json(unsigned)
        or private.get("model_outputs_read") is not False
        or private.get("provider_calls_made") != 0
        or not isinstance(pairs, list)
        or len(pairs) != PAIR_COUNT
    ):
        raise PrimaryBenchmarkError("private key identity or provenance drifted")
    task_by_id = {task["task_id"]: task for task in public["tasks"]}
    context_builder = target_free_context_builder or _target_free_prompt_context
    seen_tasks: set[str] = set()
    seen_candidates: set[int] = set()
    position_counts = {raw: [0] * RAW_ACTION_COUNT for raw in range(RAW_ACTION_COUNT)}
    for pair, slot in zip(pairs, plan["pair_slots"], strict=True):
        if pair.get("pair_ordinal") != slot["pair_ordinal"]:
            raise PrimaryBenchmarkError("private pair ordering drifted")
        if (
            pair.get("construction_stratum") != slot["construction_stratum"]
            or pair.get("within_stratum_rank") != slot["within_stratum_rank"]
            or pair.get("condition_order")
            != [slot["phase_1_arm"], slot["phase_2_arm"]]
        ):
            raise PrimaryBenchmarkError("private pair slot binding drifted")
        world_binding = pair.get("world_binding")
        prompt_context = pair.get("prompt_context")
        if (
            not isinstance(world_binding, Mapping)
            or world_binding.get("development_only") is not True
            or type(world_binding.get("candidate_index")) is not int
            or type(world_binding.get("world_seed")) is not int
            or not _is_sha256(world_binding.get("parent_canonical_hash"))
            or not isinstance(prompt_context, Mapping)
            or not isinstance(prompt_context.get("parent"), str)
            or dsl.canonical_hash(dsl.parse_sexpr(prompt_context["parent"]))
            != world_binding.get("parent_canonical_hash")
        ):
            raise PrimaryBenchmarkError("private target-free world binding drifted")
        candidate_index = int(world_binding["candidate_index"])
        if candidate_index in seen_candidates:
            raise PrimaryBenchmarkError("private cohort reuses a development world")
        seen_candidates.add(candidate_index)
        _, replayed_context = context_builder(
            int(world_binding["world_seed"]),
            str(world_binding["parent_canonical_hash"]),
        )
        if replayed_context != prompt_context:
            raise PrimaryBenchmarkError(
                "private public context differs from target-free world-seed replay"
            )
        order = pair.get("action_order")
        options = pair.get("option_ids_by_display_position")
        if (
            order != slot["action_order"]
            or not isinstance(options, list)
            or len(options) != RAW_ACTION_COUNT
            or len(set(options)) != RAW_ACTION_COUNT
            or any(
                not isinstance(value, str) or _OPTION_ID_RE.fullmatch(value) is None
                for value in options
            )
        ):
            raise PrimaryBenchmarkError("private option schedule drifted")
        expected_map = {options[pos]: raw for pos, raw in enumerate(order)}
        if pair.get("option_to_raw_action") != expected_map:
            raise PrimaryBenchmarkError("private option/raw bijection drifted")
        for position, raw in enumerate(order):
            position_counts[int(raw)][position] += 1
        arms = pair.get("arms")
        if not isinstance(arms, Mapping) or set(arms) != set(ARMS):
            raise PrimaryBenchmarkError("private context arms drifted")
        correct = {}
        for arm in ARMS:
            row = arms[arm]
            task_id = row.get("task_id")
            raw = row.get("correct_raw_action_indices")
            if (
                task_id not in task_by_id
                or task_id in seen_tasks
                or not isinstance(raw, list)
                or len(raw) != 1
                or type(raw[0]) is not int
                or not 0 <= raw[0] < RAW_ACTION_COUNT
                or row.get("correct_option_ids") != [
                    next(option for option, mapped in expected_map.items() if mapped == raw[0])
                ]
            ):
                raise PrimaryBenchmarkError("private task/scoring binding drifted")
            seen_tasks.add(task_id)
            correct[arm] = raw[0]
            expected_prompt = prompt_support.render_fair_choice_prompt(
                pair["prompt_context"],
                row["motif"]["motif_sexpr"],
                order,
                options,
            )
            if task_by_id[task_id]["rendered_prompt"] != expected_prompt:
                raise PrimaryBenchmarkError("public prompt differs from private design")
        if correct["context_a"] == correct["context_b"]:
            raise PrimaryBenchmarkError("private strict correct actions are not disjoint")
    if seen_tasks != set(task_by_id):
        raise PrimaryBenchmarkError("public/private tasks are not a 48-task bijection")
    if any(set(counts) - {2, 3} for counts in position_counts.values()):
        raise PrimaryBenchmarkError("24-pair display positions are not 2/3 balanced")
    expected_task_ids = [
        pair["arms"][pair["condition_order"][phase]]["task_id"]
        for phase in range(2)
        for pair in pairs
    ]
    if [task["task_id"] for task in public["tasks"]] != expected_task_ids:
        raise PrimaryBenchmarkError("public two-phase schedule drifted")
    if private.get("baseline_report") != _build_baseline_report(pairs):
        raise PrimaryBenchmarkError("target-blind baseline report drifted")
    expected_commitment = _sha256_json(
        {
            "protocol_id": PROTOCOL_ID,
            "config_file_sha256": plan["config_file_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "pairs": pairs,
            "baseline_report": private["baseline_report"],
        }
    )
    if private.get("private_design_commitment_sha256") != expected_commitment:
        raise PrimaryBenchmarkError("private design commitment drifted")


def validate_construction_result(
    result: Mapping[str, Any],
    public: Mapping[str, Any],
    private: Mapping[str, Any],
) -> None:
    validate_public_manifest(public)
    unsigned = {
        key: value for key, value in result.items() if key != "construction_result_sha256"
    }
    artifacts = result.get("artifacts")
    shard_validation = result.get("shard_validation")
    selection = result.get("selection")
    pairs = private.get("pairs")
    private_unsigned = {
        key: value for key, value in private.items() if key != "private_key_sha256"
    }
    if (
        result.get("schema_version") != ARTIFACT_SCHEMA_VERSION
        or result.get("kind") != CONSTRUCTION_RESULT_KIND
        or result.get("protocol_id") != PROTOCOL_ID
        or result.get("world_layer_label") != WORLD_LAYER_LABEL
        or result.get("model_response_layer_label") != MODEL_RESPONSE_LAYER_LABEL
        or result.get("independent_heldout_confirmation") is not False
        or result.get("evidence") is not False
        or result.get("classification")
        != "strict_q6_masked_benchmark_construction_complete"
        or result.get("source_manifest_sha256")
        != public.get("source_manifest_sha256")
        or result.get("source_manifest_sha256")
        != private.get("source_manifest_sha256")
        or result.get("config_file_sha256") != public.get("config_file_sha256")
        or result.get("config_file_sha256") != private.get("config_file_sha256")
        or result.get("plan_sha256") != public.get("plan_sha256")
        or result.get("plan_sha256") != private.get("plan_sha256")
        or result.get("reviewed_plan_sha256") != result.get("plan_sha256")
        or private.get("kind") != PRIVATE_KEY_KIND
        or private.get("world_layer_label") != WORLD_LAYER_LABEL
        or private.get("model_response_layer_label") != MODEL_RESPONSE_LAYER_LABEL
        or private.get("private_key_sha256") != _sha256_json(private_unsigned)
        or private.get("public_manifest_sha256")
        != public.get("public_manifest_sha256")
        or result.get("final_benchmark_minted") is not True
        or result.get("model_outputs_read") is not False
        or result.get("provider_calls_made") != 0
        or result.get("provider_calls_authorized") is not False
        or not _is_sha256(result.get("reviewed_plan_file_sha256"))
        or result.get("reviewed_plan_file_sha256")
        != private.get("reviewed_plan_file_sha256")
        or result.get("construction_result_sha256") != _sha256_json(unsigned)
        or not isinstance(artifacts, Mapping)
        or not isinstance(shard_validation, Mapping)
        or not isinstance(selection, Mapping)
        or not isinstance(pairs, list)
        or len(pairs) != PAIR_COUNT
        or shard_validation.get("validated_shard_count") != 128
        or shard_validation.get("validated_world_count") != 1024
        or shard_validation.get("all_shards_hash_and_schema_validated") is not True
    ):
        raise PrimaryBenchmarkError("construction result identity or barrier drifted")
    selected_indices = [
        pair.get("world_binding", {}).get("candidate_index")
        if isinstance(pair, Mapping)
        and isinstance(pair.get("world_binding"), Mapping)
        else None
        for pair in pairs
    ]
    expected_counts = {
        stratum: sum(
            isinstance(pair, Mapping) and pair.get("construction_stratum") == stratum
            for pair in pairs
        )
        for stratum in spark_lineage.MOTIF_STRATA
    }
    if (
        selection.get("tier_id") != feasibility.STRICT_TIER
        or selection.get("selected_q") != TARGET_PER_STRATUM
        or selection.get("world_count") != PAIR_COUNT
        or selection.get("task_count") != TASK_COUNT
        or selection.get("selected_candidate_indices_in_pair_slot_order")
        != selected_indices
        or len(set(selected_indices)) != PAIR_COUNT
        or selection.get("counts_by_construction_stratum") != expected_counts
        or expected_counts
        != {stratum: TARGET_PER_STRATUM for stratum in spark_lineage.MOTIF_STRATA}
    ):
        raise PrimaryBenchmarkError("construction selection summary drifted")
    if (
        artifacts.get("public_manifest_sha256") != public.get("public_manifest_sha256")
        or artifacts.get("private_key_sha256") != private.get("private_key_sha256")
        or artifacts.get("private_design_commitment_sha256")
        != public.get("private_design_commitment_sha256")
        or artifacts.get("public_manifest_file_sha256")
        != _sha256_bytes(_rendered_json_bytes(public))
        or artifacts.get("private_key_file_sha256")
        != _sha256_bytes(_rendered_json_bytes(private))
    ):
        raise PrimaryBenchmarkError("construction artifact cross-binding drifted")


def _emit_json_exclusive_0600(value: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PrimaryBenchmarkError(f"refusing to overwrite artifact {output}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(_rendered_json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def write_benchmark_artifacts(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    result: Mapping[str, Any],
    output_directory: str | Path,
    *,
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    target_free_context_builder: Callable[
        [int, str | None], tuple[Any, dict[str, Any]]
    ]
    | None = None,
) -> dict[str, Path]:
    """Write the three construction artifacts with exclusive mode-0600 creates."""

    validate_private_key(
        private,
        public,
        config=config,
        plan=plan,
        target_free_context_builder=target_free_context_builder,
    )
    validate_construction_result(result, public, private)
    directory = Path(output_directory)
    paths = {
        "public": directory / "public.json",
        "private": directory / "private.json",
        "result": directory / "result.json",
    }
    if any(path.exists() for path in paths.values()):
        raise PrimaryBenchmarkError("refusing to overwrite benchmark artifacts")
    for name in ("public", "private", "result"):
        _emit_json_exclusive_0600(
            {"public": public, "private": private, "result": result}[name],
            paths[name],
        )
    return paths


def _read_config(path: Path) -> tuple[dict[str, Any], str]:
    config, payload = _read_json(path, "benchmark config")
    validate_config(config)
    return config, _sha256_bytes(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline strict-q6 benchmark construction")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    plan_parser.add_argument("--output", type=Path, required=True)
    construct_parser = subparsers.add_parser("construct")
    construct_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    construct_parser.add_argument("--plan", type=Path, required=True)
    construct_parser.add_argument("--reviewed-plan-sha256", required=True)
    construct_parser.add_argument("--reviewed-plan-file-sha256", required=True)
    construct_parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    config, config_sha = _read_config(args.config)
    if args.command == "plan":
        observed_source = source_manifest(PROJECT_ROOT)
        source_head, _ = _assert_clean_source_freeze(PROJECT_ROOT, observed_source)
        plan = build_construction_plan(
            config,
            config_file_sha256=config_sha,
            source_manifest_sha256=str(observed_source["source_manifest_sha256"]),
            source_freeze_git_head=source_head,
        )
        _emit_json_exclusive_0600(plan, args.output)
        return 0
    plan, _ = _read_json(args.plan, "construction plan")
    public, private, result = construct_benchmark(
        config,
        plan,
        config_file_sha256=config_sha,
        reviewed_plan_sha256=args.reviewed_plan_sha256,
        reviewed_plan_file_sha256=args.reviewed_plan_file_sha256,
        plan_path=args.plan,
    )
    write_benchmark_artifacts(
        public,
        private,
        result,
        args.output_directory,
        config=config,
        plan=plan,
    )
    return 0


__all__ = [
    "ARMS",
    "CONFIG_KIND",
    "CONSTRUCTION_RESULT_KIND",
    "MODEL_RESPONSE_LAYER_LABEL",
    "PAIR_COUNT",
    "PLAN_KIND",
    "PRIVATE_KEY_KIND",
    "PROTOCOL_ID",
    "PUBLIC_MANIFEST_KIND",
    "PrimaryBenchmarkError",
    "TASK_COUNT",
    "WORLD_LAYER_LABEL",
    "action_order_for_pair",
    "build_construction_plan",
    "construct_benchmark",
    "main",
    "validate_config",
    "validate_construction_plan",
    "validate_construction_result",
    "validate_private_key",
    "validate_public_manifest",
    "write_benchmark_artifacts",
]


if __name__ == "__main__":
    raise SystemExit(main())
