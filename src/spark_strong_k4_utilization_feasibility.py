"""Offline feasibility scan for the next strong-K4 utilization design.

This module is intentionally independent from the sealed v2 scan and from the
fair-choice benchmark.  It owns a target-free 1024-seed plan, an eight-world
shard scan over the complete 105-motif/ten-action library, and a deterministic
merge that reports creation geometry and strict/degraded utilization-pair
capacity.  No provider boundary exists in this file.

The scan is a development construction diagnostic.  It must never be read as a
model result, a natural-world prevalence estimate, or a newly minted
benchmark.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import dsl, spark_closure, spark_lineage
from .provenance import PROJECT_ROOT, source_manifest
from .spark_compressor import SparkCompressor
from .spark_lineage import EditAction, LineageRecord, select_parent
from .spark_strong_k4_scan import (
    _BehaviorCachedCompressor,
    _analyze_action,
    _lineage_index,
    _raw_actions,
)
from .spark_world import SparkWorld, generate_spark_world


SCHEMA_VERSION = 1
PROTOCOL_ID = "spark-strong-k4-utilization-feasibility-v2"
RETIRED_PROTOCOL_ID = "spark-strong-k4-utilization-feasibility-v1"
RETIRED_WORLD_SEED_NAMESPACE = f"{RETIRED_PROTOCOL_ID}:world-seed"
RETIRED_SEED_VECTOR_SHA256 = (
    "58abdd2e86f1fd7bdcbcde2b67280b433a13044ed3035866d9cc2548b510ea38"
)
RETIRED_MATERIALIZED_WORLD_SEED = 3092638349656038141
CONFIG_KIND = "spark-strong-k4-utilization-feasibility-config"
PLAN_KIND = "spark-strong-k4-utilization-feasibility-plan"
SHARD_KIND = "spark-strong-k4-utilization-feasibility-shard"
MERGED_KIND = "spark-strong-k4-utilization-feasibility-result"
ENDPOINT_NAMES = ("K1", "K2", "K3", "K4_full_pool")
MOTIF_COUNT = 105
WORLD_COUNT = 1024
SHARD_WORLD_COUNT = 8
RAW_ACTION_COUNT = 10
STRICT_TIER = "strict_unique_nonconstant_switch"
DEGRADED_TIER = "degraded_two_choice_disjoint_switch"
TIER_IDS = (STRICT_TIER, DEGRADED_TIER)
SEED_MASK_63 = (1 << 63) - 1
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / f"{PROTOCOL_ID}.json"
DEFAULT_RESERVATION_PATH = (
    PROJECT_ROOT / "configs" / f"{PROTOCOL_ID}-seeds.json"
)
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "configs" / "development-seed-registry.json"


class UtilizationFeasibilityError(ValueError):
    """Raised when a frozen utilization input or result fails closed."""


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
        raise UtilizationFeasibilityError("value is not canonical JSON") from exc


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


_REVIEWED_PLAN_GUARD = object()


class _ReviewedPlanAuthorization:
    """Internal capability minted only after the reviewed-plan gate passes."""

    __slots__ = ("plan_sha256", "_guard")

    def __init__(self, plan_sha256: str, guard: object) -> None:
        if guard is not _REVIEWED_PLAN_GUARD or not _is_sha256(plan_sha256):
            raise UtilizationFeasibilityError("reviewed-plan authorization is invalid")
        self.plan_sha256 = plan_sha256
        self._guard = guard


def _bound_file_sha256(relative_path: object, expected: object) -> None:
    """Check one immutable upstream/config binding against repository bytes."""

    if not isinstance(relative_path, str) or Path(relative_path).is_absolute():
        raise UtilizationFeasibilityError("bound file path is malformed")
    if not _is_sha256(expected):
        raise UtilizationFeasibilityError("bound file digest is malformed")
    path = PROJECT_ROOT / relative_path
    try:
        observed = _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise UtilizationFeasibilityError(
            f"bound file is unavailable: {relative_path}"
        ) from exc
    if observed != expected:
        raise UtilizationFeasibilityError(
            f"bound file bytes differ: {relative_path}"
        )


def derive_candidate_world_seed(namespace: str, candidate_index: int) -> int:
    """Derive the frozen 63-bit world seed for one candidate index."""

    if not isinstance(namespace, str) or not namespace:
        raise UtilizationFeasibilityError("world-seed namespace must be non-empty")
    if type(candidate_index) is not int or candidate_index < 0:
        raise UtilizationFeasibilityError("candidate index must be non-negative")
    digest = hashlib.sha256(f"{namespace}:{candidate_index}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") & SEED_MASK_63


def _motif_identity_rows() -> tuple[dict[str, Any], ...]:
    rows = []
    for motif in spark_lineage.build_motif_library():
        rows.append(
            {
                "motif_id": motif.motif_id,
                "motif_sexpr": dsl.to_sexpr(motif.ast),
                "motif_canonical_hash": motif.canonical_hash,
                "motif_behavior_hash": dsl.behavior_hash(motif.ast),
                "stratum": motif.stratum,
                "complexity_bucket": list(motif.complexity_bucket),
            }
        )
    return tuple(rows)


def motif_library_identity() -> dict[str, Any]:
    """Return the complete target-independent 105-motif identity."""

    rows = _motif_identity_rows()
    counts = Counter(str(row["stratum"]) for row in rows)
    return {
        "count": len(rows),
        "sha256": _sha256_json(list(rows)),
        "motif_strata_in_frozen_order": list(spark_lineage.MOTIF_STRATA),
        "motif_counts_by_stratum": {
            stratum: counts[stratum] for stratum in spark_lineage.MOTIF_STRATA
        },
        "complexity_bucket": [2, 3],
    }


def enumerate_full_motif_library() -> tuple[dict[str, Any], ...]:
    """Enumerate the full library in canonical motif-id order."""

    return _motif_identity_rows()


# Short alias useful to callers that treat the library as a pure seam.
full_motif_library = enumerate_full_motif_library


def derive_candidate_seed_vector(config: Mapping[str, Any]) -> tuple[int, ...]:
    stream = config.get("candidate_stream")
    if not isinstance(stream, Mapping):
        raise UtilizationFeasibilityError("candidate_stream is malformed")
    start = stream.get("candidate_index_start")
    count = stream.get("candidate_world_count_cap")
    namespace = stream.get("world_seed_namespace")
    if type(start) is not int or type(count) is not int or start < 0 or count != WORLD_COUNT:
        raise UtilizationFeasibilityError("candidate range is malformed")
    if not isinstance(namespace, str):
        raise UtilizationFeasibilityError("candidate namespace is malformed")
    return tuple(
        derive_candidate_world_seed(namespace, index)
        for index in range(start, start + count)
    )


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate scientific constants and the exact frozen motif/seed identities."""

    if not isinstance(config, Mapping):
        raise UtilizationFeasibilityError("config must be an object")
    if (
        config.get("schema_version") != SCHEMA_VERSION
        or config.get("kind") != CONFIG_KIND
        or config.get("protocol_id") != PROTOCOL_ID
    ):
        raise UtilizationFeasibilityError("utilization config identity drifted")
    stream = config.get("candidate_stream")
    context = config.get("context_universe")
    action = config.get("action_universe")
    endpoint = config.get("endpoint")
    creation = config.get("opportunity_creation_census")
    tiers = config.get("utilization_pair_tiers")
    cohort = config.get("cohort_feasibility")
    barrier = config.get("target_free_plan_barrier")
    materialization = config.get("development_target_materialization")
    contract = config.get("scan_and_merge_contract")
    artifact = config.get("artifact_contract")
    reservation = config.get("seed_reservation")
    if not all(
        isinstance(value, Mapping)
        for value in (
            stream,
            context,
            action,
            endpoint,
            creation,
            tiers,
            cohort,
            barrier,
            materialization,
            contract,
            artifact,
            reservation,
        )
    ):
        raise UtilizationFeasibilityError("utilization config sections are malformed")
    assert isinstance(stream, Mapping)
    assert isinstance(context, Mapping)
    assert isinstance(action, Mapping)
    assert isinstance(endpoint, Mapping)
    assert isinstance(creation, Mapping)
    assert isinstance(tiers, Mapping)
    assert isinstance(cohort, Mapping)
    assert isinstance(barrier, Mapping)
    assert isinstance(materialization, Mapping)
    assert isinstance(contract, Mapping)
    assert isinstance(artifact, Mapping)
    assert isinstance(reservation, Mapping)

    expected = (
        (
            config.get("evidence_scope"),
            "offline_outcome_conditioned_development_opportunity_census_and_"
            "utilization_construction_only",
        ),
        (stream.get("candidate_index_start"), 0),
        (stream.get("candidate_world_count_cap"), WORLD_COUNT),
        (stream.get("fixed_full_scan_required"), True),
        (stream.get("outcome_dependent_early_stop_allowed"), False),
        (stream.get("stage_world_count"), SHARD_WORLD_COUNT),
        (
            stream.get("complete_range"),
            {"start": 0, "count": WORLD_COUNT, "end_exclusive": WORLD_COUNT},
        ),
        (stream.get("world_seed_namespace"), f"{PROTOCOL_ID}:world-seed"),
        (
            stream.get("world_seed_rule"),
            "sha256(world_seed_namespace + ':' + decimal_candidate_index), first 8 bytes "
            "big-endian, mask to 63 bits",
        ),
        (
            stream.get("collision_exclusions"),
            "all seeds in the bound historical registry and the fully retired "
            "utilization-feasibility-v1 namespace; zero collisions in the frozen v2 "
            "0..1023 vector",
        ),
        (stream.get("incomplete_classification"), "scan_incomplete_not_infeasible"),
        (
            context.get("candidate_rule"),
            "every member of the frozen target-independent motif library in ascending "
            "motif_id order",
        ),
        (context.get("candidates_per_world"), MOTIF_COUNT),
        (context.get("motif_strata_in_frozen_order"), list(spark_lineage.MOTIF_STRATA)),
        (
            context.get("motif_counts_by_stratum"),
            {
                "affine_commutative": 21,
                "affine_directional": 42,
                "affine_multiplicative": 21,
                "pairwise_variable": 21,
            },
        ),
        (context.get("complexity_bucket"), [2, 3]),
        (context.get("motif_selection_reads_target_or_outcome"), False),
        (context.get("motif_redraws_per_world"), 0),
        (context.get("unscanned_motifs_may_be_treated_as_no_opportunity"), False),
        (action.get("raw_actions_per_context"), RAW_ACTION_COUNT),
        (action.get("action_paths"), [[1, 1], [1, 2]]),
        (
            action.get("action_frames_per_path_in_raw_order"),
            ["replace", "add-right", "sub-right", "sub-left", "mul-right"],
        ),
        (
            action.get("raw_action_index_rule"),
            "five frames in frozen order for path [1,1], followed by the same five frames "
            "for path [1,2]",
        ),
        (action.get("all_actions_scored_symmetrically"), True),
        (endpoint.get("name"), "K4_full_pool"),
        (endpoint.get("historical_endpoints_reclassified"), False),
        (endpoint.get("closure_max_rounds"), spark_closure.CLOSURE_MAX_ROUNDS),
        (endpoint.get("requires_K3"), True),
        (endpoint.get("minimum_unique_control_behaviors"), 3),
        (endpoint.get("all_unique_controls_must_fail_exact_identification"), True),
        (endpoint.get("behavior_equivalent_controls_must_have_identical_endpoint"), True),
        (
            endpoint.get("nonconstant_child_definition"),
            "the complete-domain binary behavior contains both 0 and 1",
        ),
        (endpoint.get("constant_K4_excluded_from_every_utilization_tier"), True),
        (
            creation.get("context_pair_universe"),
            "all unordered behavior-distinct motif pairs with the same stratum and "
            "complexity bucket",
        ),
        (creation.get("opportunity_count_unit"), "raw syntactic action"),
        (
            creation.get("pair_change_outputs"),
            [
                "K1 raw-action-set equality and symmetric-difference size",
                "K2 raw-action-set equality and symmetric-difference size",
                "nonconstant K4 raw-action-set equality and symmetric-difference size",
                "constant K4 counts reported separately",
            ],
        ),
        (creation.get("directional_factual_or_sham_label_assigned"), False),
        (creation.get("p_values_or_hypothesis_classification_produced"), False),
        (creation.get("natural_world_prevalence_estimated"), False),
        (reservation.get("kind"), "spark-development-seed-reservation"),
        (
            reservation.get("retired_namespace_binding"),
            {
                "protocol_id": RETIRED_PROTOCOL_ID,
                "world_seed_namespace": RETIRED_WORLD_SEED_NAMESPACE,
                "candidate_seed_vector_sha256": RETIRED_SEED_VECTOR_SHA256,
                "candidate_world_count": WORLD_COUNT,
                "status": "retired_pre_plan_implementation_smoke",
                "entire_namespace_retired": True,
            },
        ),
        (barrier.get("must_bind_exact_config_file_sha256"), True),
        (barrier.get("must_bind_exact_seed_reservation_file_sha256"), True),
        (barrier.get("must_bind_current_source_manifest_sha256"), True),
        (barrier.get("must_be_generated_after_source_freeze_commit"), True),
        (barrier.get("target_materialized"), False),
        (barrier.get("compressor_run"), False),
        (barrier.get("model_outputs_read"), False),
        (barrier.get("provider_calls_made"), 0),
        (barrier.get("scan_authorized_before_independent_plan_review"), False),
        (barrier.get("plan_artifact_generated_during_this_code_change"), False),
        (
            barrier.get("post_review_scan_authorization"),
            "scan CLI and build_scan_shard require the exact independently reviewed "
            "plan_sha256 as a separate explicit argument",
        ),
        (
            barrier.get(
                "lower_level_materialization_requires_internal_authorization_capability"
            ),
            True,
        ),
        (materialization.get("target_seed_namespace"), PROTOCOL_ID),
        (
            materialization.get("target_seed_rule"),
            "sha256(target_seed_namespace + ':target:' + decimal_world_seed), full 32-byte "
            "digest interpreted as one big-endian integer",
        ),
        (
            materialization.get("target_selection_rule"),
            "after the reviewed plan barrier, call existing generate_spark_world(world_seed, "
            "target_seed) exactly once; its existing random.Random(target_seed).randrange(256) "
            "selects the sole target",
        ),
        (materialization.get("target_draws_per_world"), 1),
        (materialization.get("target_redraws"), 0),
        (materialization.get("candidate_replacement_on_construction_outcome"), False),
        (
            materialization.get("all_materialized_worlds_status"),
            "development_only_never_confirmatory",
        ),
        (
            contract.get("evaluation_order"),
            "candidate_index, then motif_id, then raw_action_index, all ascending",
        ),
        (contract.get("shards_must_be_aligned_contiguous_and_nonoverlapping"), True),
        (contract.get("merge_requires_complete_fixed_range"), True),
        (
            contract.get("strict_and_degraded_tiers_evaluated_independently_after_complete_merge"),
            True,
        ),
        (contract.get("no_result_based_seed_motif_or_tier_replacement"), True),
        (tiers.get("tier_mixing_allowed"), False),
        (tiers.get("selection_after_partial_scan_allowed"), False),
        (tiers.get("K2_opportunity_count_unit"), "raw syntactic action"),
        (artifact.get("evidence"), False),
        (artifact.get("confirmatory"), False),
        (artifact.get("model_outputs_read"), False),
        (artifact.get("provider_calls_made"), 0),
        (artifact.get("all_scanned_worlds_development_only"), True),
        (artifact.get("exclusive_create_no_overwrite"), True),
        (artifact.get("private_outputs_mode"), "0600"),
        (cohort.get("independent_unit"), "unique development world"),
        (cohort.get("one_pair_and_one_construction_stratum_assignment_per_world"), True),
        (cohort.get("target_pairs_per_stratum_for_geometry"), 8),
        (cohort.get("target_total_pairs_for_geometry"), 32),
        (cohort.get("fallback_pairs_per_stratum_for_geometry"), 4),
        (cohort.get("fallback_total_pairs_for_geometry"), 16),
        (
            cohort.get("geometry_outputs"),
            [
                "maximum exact stratum-balanced q under the cap",
                "target-q feasibility, fallback-q feasibility, and selected q reported separately",
                "candidate capacity by tier, stratum and unordered correct-raw set pair",
                "correct raw, path and frame marginals",
                "child behavior and full-pool bundle diversity",
            ],
        ),
        (cohort.get("final_pair_count_set_by_this_scan"), False),
        (cohort.get("final_benchmark_minted_by_this_scan"), False),
        (cohort.get("world_capacity"), None),
    )
    if any(actual != wanted for actual, wanted in expected[:-1]):
        raise UtilizationFeasibilityError("utilization config constants drifted")
    if "world_capacity" in cohort and cohort.get("world_capacity") not in (None, 1):
        raise UtilizationFeasibilityError("world capacity must be one")
    identity = motif_library_identity()
    if (
        context.get("motif_library_sha256") != identity["sha256"]
        or context.get("candidates_per_world") != identity["count"]
        or context.get("motif_counts_by_stratum") != identity["motif_counts_by_stratum"]
    ):
        raise UtilizationFeasibilityError("motif library identity drifted")
    seeds = derive_candidate_seed_vector(config)
    if len(seeds) != WORLD_COUNT or len(set(seeds)) != WORLD_COUNT:
        raise UtilizationFeasibilityError("candidate seed vector is not unique")
    if stream.get("candidate_seed_vector_sha256") != _sha256_bytes(
        json.dumps(seeds, separators=(",", ":")).encode("ascii")
    ):
        raise UtilizationFeasibilityError("candidate seed vector digest drifted")
    if reservation.get("relative_path") != f"configs/{PROTOCOL_ID}-seeds.json":
        raise UtilizationFeasibilityError("seed reservation path drifted")
    if reservation.get("reservation_must_match_candidate_vector") is not True:
        raise UtilizationFeasibilityError("seed reservation binding is not strict")
    if reservation.get("reserved_worlds_are_development_only_forever") is not True:
        raise UtilizationFeasibilityError("seed reservation development boundary drifted")
    if config.get("utilization_pair_tiers", {}).get("evaluation_order") != list(TIER_IDS):
        raise UtilizationFeasibilityError("tier evaluation order drifted")
    if tiers.get("shared_requirements") != {
        "same_world_D0_parent_and_action_universe": True,
        "same_motif_stratum": True,
        "same_complexity_bucket": True,
        "distinct_motif_id": True,
        "distinct_complete_domain_motif_behavior": True,
        "K2_opportunity_counts_equal": True,
        "constant_K4_count_each_arm": 0,
        "world_capacity": 1,
    }:
        raise UtilizationFeasibilityError("pair shared requirements drifted")
    tier_rows = tiers.get("tiers")
    if (
        not isinstance(tier_rows, list)
        or [row.get("tier_id") for row in tier_rows if isinstance(row, Mapping)] != list(TIER_IDS)
        or any(
            not isinstance(row, Mapping)
            or row.get("correct_raw_action_sets_must_be_disjoint") is not True
            or row.get("correct_raw_actions_must_differ") is not True
            for row in tier_rows
        )
    ):
        raise UtilizationFeasibilityError("pair tier definitions drifted")
    expected_tier_counts = (1, 2)
    if any(
        row.get("nonconstant_K4_raw_action_count_each_arm") != count
        for row, count in zip(tier_rows, expected_tier_counts, strict=True)
    ):
        raise UtilizationFeasibilityError("pair tier K4 cardinalities drifted")
    expected_tier_labels = (
        (
            1,
            "strict_unique_switch_geometry_feasible",
            "strict_unique_switch_geometry_infeasible_under_cap",
        ),
        (
            2,
            "degraded_equal_two_choice_switch_geometry_feasible",
            "degraded_equal_two_choice_switch_geometry_infeasible_under_cap",
        ),
    )
    if any(
        (
            row.get("priority"),
            row.get("feasible_classification"),
            row.get("infeasible_classification"),
        )
        != labels
        for row, labels in zip(tier_rows, expected_tier_labels, strict=True)
    ):
        raise UtilizationFeasibilityError("pair tier labels drifted")
    upstream = config.get("upstream_calibration")
    if not isinstance(upstream, Mapping):
        raise UtilizationFeasibilityError("upstream calibration binding is malformed")
    if (
        upstream.get("old_protocols_and_artifacts_immutable") is not True
        or upstream.get("old_development_worlds_eligible_for_new_cohort") is not False
    ):
        raise UtilizationFeasibilityError("upstream immutability boundary drifted")
    sealed_files = upstream.get("sealed_files")
    if not isinstance(sealed_files, list) or len(sealed_files) != 5:
        raise UtilizationFeasibilityError("upstream sealed-file binding is malformed")
    for item in sealed_files:
        if not isinstance(item, Mapping):
            raise UtilizationFeasibilityError("upstream sealed-file row is malformed")
        _bound_file_sha256(item.get("relative_path"), item.get("file_sha256"))
    reservation_binding = config["seed_reservation"]
    _bound_file_sha256(
        reservation_binding.get("base_registry_relative_path"),
        reservation_binding.get("base_registry_file_sha256"),
    )


def validate_seed_reservation(
    config: Mapping[str, Any],
    reservation: Mapping[str, Any],
    registry: Mapping[str, Any] | None = None,
    *,
    registry_file_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the exact reserved vector and, when supplied, registry collision."""

    validate_config(config)
    if not isinstance(reservation, Mapping):
        raise UtilizationFeasibilityError("seed reservation must be an object")
    expected_seeds = list(derive_candidate_seed_vector(config))
    retired_seeds = [
        derive_candidate_world_seed(RETIRED_WORLD_SEED_NAMESPACE, index)
        for index in range(WORLD_COUNT)
    ]
    retired_vector_sha256 = _sha256_bytes(
        json.dumps(retired_seeds, separators=(",", ":")).encode("ascii")
    )
    expected_retired_namespaces = [
        {
            "protocol_id": RETIRED_PROTOCOL_ID,
            "status": "retired_pre_plan_implementation_smoke",
            "candidate_range": {
                "start": 0,
                "count": WORLD_COUNT,
                "end_exclusive": WORLD_COUNT,
            },
            "world_seed_namespace": RETIRED_WORLD_SEED_NAMESPACE,
            "world_seed_rule": (
                "sha256(world_seed_namespace + ':' + decimal_candidate_index), first 8 "
                "bytes big-endian, mask to 63 bits"
            ),
            "candidate_seed_vector_sha256": RETIRED_SEED_VECTOR_SHA256,
            "materialized_before_reviewed_plan": [
                {
                    "candidate_index": 0,
                    "world_seed": RETIRED_MATERIALIZED_WORLD_SEED,
                    "target_materialization_count": 1,
                    "compressor_run": True,
                    "context_count": MOTIF_COUNT,
                    "raw_action_evaluation_count": MOTIF_COUNT * RAW_ACTION_COUNT,
                    "artifact_persisted": False,
                    "model_or_provider_calls": 0,
                }
            ],
            "entire_namespace_retired": True,
            "future_use": (
                "all 1024 seeds in this namespace are permanently excluded; candidate 0 "
                "must not be dropped or replaced to repair the stream"
            ),
        }
    ]
    if (
        retired_vector_sha256 != RETIRED_SEED_VECTOR_SHA256
        or retired_seeds[0] != RETIRED_MATERIALIZED_WORLD_SEED
        or reservation.get("schema_version") != SCHEMA_VERSION
        or reservation.get("kind") != "spark-development-seed-reservation"
        or reservation.get("protocol_id") != PROTOCOL_ID
        or reservation.get("status") != "reserved_target_free_plan_not_generated"
        or reservation.get("base_registry") != {
            "relative_path": config["seed_reservation"]["base_registry_relative_path"],
            "file_sha256": config["seed_reservation"]["base_registry_file_sha256"],
            "historical_seed_count": 2152,
        }
        or reservation.get("retired_namespaces") != expected_retired_namespaces
        or reservation.get("candidate_range")
        != {"start": 0, "count": WORLD_COUNT, "end_exclusive": WORLD_COUNT}
        or reservation.get("world_seed_namespace")
        != config["candidate_stream"]["world_seed_namespace"]
        or reservation.get("candidate_seed_vector_sha256")
        != config["candidate_stream"]["candidate_seed_vector_sha256"]
        or reservation.get("historical_collision_count") != 0
        or reservation.get("retired_namespace_collision_count") != 0
        or reservation.get("active_vector_targets_materialized_before_reviewed_plan") != 0
        or reservation.get("model_or_provider_calls") != 0
        or reservation.get("seeds") != expected_seeds
        or len(set(expected_seeds)) != WORLD_COUNT
        or set(expected_seeds).intersection(retired_seeds)
    ):
        raise UtilizationFeasibilityError("seed reservation does not bind the exact vector")
    if registry is not None:
        registered = registry.get("seeds")
        if not isinstance(registered, list) or any(type(seed) is not int for seed in registered):
            raise UtilizationFeasibilityError("base seed registry is malformed")
        if len(registered) != reservation["base_registry"]["historical_seed_count"]:
            raise UtilizationFeasibilityError("base registry count drifted")
        if set(registered).intersection(expected_seeds):
            raise UtilizationFeasibilityError("reserved candidate collides with historical seed")
        if (
            registry_file_sha256 is not None
            and registry_file_sha256
            != config["seed_reservation"]["base_registry_file_sha256"]
        ):
            raise UtilizationFeasibilityError("base registry file digest drifted")
    return {
        "candidate_seed_count": WORLD_COUNT,
        "candidate_seed_vector_sha256": reservation["candidate_seed_vector_sha256"],
        "registry_collision_count": 0,
        "retired_namespace_count": 1,
        "retired_seed_count": WORLD_COUNT,
        "retired_namespace_collision_count": 0,
        "retired_materialized_candidate_count": 1,
        "base_registry_file_sha256": config["seed_reservation"]["base_registry_file_sha256"],
        "base_registry_seed_count": reservation["base_registry"]["historical_seed_count"],
        "reservation_exact": True,
    }


def _validate_digest(value: object, label: str) -> str:
    if not _is_sha256(value):
        raise UtilizationFeasibilityError(f"{label} digest is malformed")
    return str(value)


def build_target_free_scan_plan(
    config: Mapping[str, Any],
    reservation: Mapping[str, Any],
    *,
    config_file_sha256: str,
    seed_reservation_file_sha256: str,
    source_manifest_sha256: str,
    registry: Mapping[str, Any] | None = None,
    registry_file_sha256: str | None = None,
) -> dict[str, Any]:
    """Freeze only seeds and the full motif-library identity."""

    validate_config(config)
    if registry is None or registry_file_sha256 is None:
        raise UtilizationFeasibilityError(
            "target-free plan requires the bound historical seed registry"
        )
    registry_audit = validate_seed_reservation(
        config,
        reservation,
        registry,
        registry_file_sha256=registry_file_sha256,
    )
    config_sha = _validate_digest(config_file_sha256, "config")
    reservation_sha = _validate_digest(seed_reservation_file_sha256, "seed reservation")
    source_sha = _validate_digest(source_manifest_sha256, "source manifest")
    seeds = derive_candidate_seed_vector(config)
    library = motif_library_identity()
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": config["evidence_scope"],
        "config_file_sha256": config_sha,
        "seed_reservation_file_sha256": reservation_sha,
        "source_manifest_sha256": source_sha,
        "candidate_seed_vector_sha256": config["candidate_stream"]["candidate_seed_vector_sha256"],
        "candidate_range": {"start": 0, "count": WORLD_COUNT, "end_exclusive": WORLD_COUNT},
        "candidates": [
            {"candidate_index": index, "world_seed": seed}
            for index, seed in enumerate(seeds)
        ],
        "motif_library": library,
        "registry_audit": registry_audit,
        "target_materialized": False,
        "compressor_run": False,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "scan_authorized_before_independent_plan_review": False,
        "outcome_conditioned_benchmark_construction": False,
        "development_only": True,
    }
    return {**unsigned, "plan_sha256": _sha256_json(unsigned)}


def validate_scan_plan(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    config_file_sha256: str,
    require_current_source: bool = True,
) -> tuple[Mapping[str, Any], ...]:
    validate_config(config)
    config_sha = _validate_digest(config_file_sha256, "config")
    expected_keys = {
        "schema_version", "kind", "protocol_id", "evidence_scope",
        "config_file_sha256", "seed_reservation_file_sha256", "source_manifest_sha256",
        "candidate_seed_vector_sha256", "candidate_range", "candidates", "motif_library",
        "registry_audit", "target_materialized", "compressor_run", "model_outputs_read",
        "provider_calls_made", "scan_authorized_before_independent_plan_review",
        "outcome_conditioned_benchmark_construction", "development_only", "plan_sha256",
    }
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    candidates = plan.get("candidates")
    seeds = derive_candidate_seed_vector(config)
    if (
        set(plan) != expected_keys
        or plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("kind") != PLAN_KIND
        or plan.get("protocol_id") != PROTOCOL_ID
        or plan.get("evidence_scope") != config.get("evidence_scope")
        or plan.get("config_file_sha256") != config_sha
        or not _is_sha256(plan.get("seed_reservation_file_sha256"))
        or plan.get("source_manifest_sha256") is None
        or not _is_sha256(plan.get("source_manifest_sha256"))
        or plan.get("candidate_seed_vector_sha256")
        != config["candidate_stream"]["candidate_seed_vector_sha256"]
        or plan.get("candidate_range")
        != {"start": 0, "count": WORLD_COUNT, "end_exclusive": WORLD_COUNT}
        or plan.get("motif_library") != motif_library_identity()
        or plan.get("registry_audit") != {
            "candidate_seed_count": WORLD_COUNT,
            "candidate_seed_vector_sha256": config["candidate_stream"][
                "candidate_seed_vector_sha256"
            ],
            "registry_collision_count": 0,
            "retired_namespace_count": 1,
            "retired_seed_count": WORLD_COUNT,
            "retired_namespace_collision_count": 0,
            "retired_materialized_candidate_count": 1,
            "base_registry_file_sha256": config["seed_reservation"]["base_registry_file_sha256"],
            "base_registry_seed_count": 2152,
            "reservation_exact": True,
        }
        or plan.get("plan_sha256") != _sha256_json(unsigned)
        or plan.get("target_materialized") is not False
        or plan.get("compressor_run") is not False
        or plan.get("model_outputs_read") is not False
        or plan.get("provider_calls_made") != 0
        or plan.get("scan_authorized_before_independent_plan_review") is not False
        or plan.get("outcome_conditioned_benchmark_construction") is not False
        or plan.get("development_only") is not True
        or not isinstance(candidates, list)
        or len(candidates) != WORLD_COUNT
    ):
        raise UtilizationFeasibilityError("target-free utilization plan is malformed or tampered")
    validated: list[Mapping[str, Any]] = []
    for index, (candidate, seed) in enumerate(zip(candidates, seeds, strict=True)):
        if candidate != {"candidate_index": index, "world_seed": seed}:
            raise UtilizationFeasibilityError("plan candidate seed/index drifted")
        validated.append(candidate)
    manifest = str(plan["source_manifest_sha256"])
    if require_current_source and _current_source_manifest_sha256() != manifest:
        raise UtilizationFeasibilityError("plan source manifest drifted")
    return tuple(validated)


def _current_source_manifest_sha256() -> str:
    value = source_manifest(PROJECT_ROOT).get("source_manifest_sha256")
    return _validate_digest(value, "current source manifest")


def derive_private_target_seed(config: Mapping[str, Any], world_seed: int) -> int:
    materialization = config.get("development_target_materialization")
    if not isinstance(materialization, Mapping):
        raise UtilizationFeasibilityError("target materialization section is malformed")
    namespace = materialization.get("target_seed_namespace")
    if not isinstance(namespace, str) or not namespace:
        raise UtilizationFeasibilityError("target namespace is malformed")
    if type(world_seed) is not int:
        raise UtilizationFeasibilityError("world seed must be an integer")
    return int.from_bytes(
        hashlib.sha256(f"{namespace}:target:{world_seed}".encode("ascii")).digest(),
        "big",
    )


def _materialize_private_candidate_world(
    config: Mapping[str, Any],
    world_seed: int,
    *,
    authorization: _ReviewedPlanAuthorization,
) -> SparkWorld:
    """Materialize exactly one development target after the plan barrier."""

    if (
        not isinstance(authorization, _ReviewedPlanAuthorization)
        or authorization._guard is not _REVIEWED_PLAN_GUARD
        or not _is_sha256(authorization.plan_sha256)
    ):
        raise UtilizationFeasibilityError(
            "target materialization requires reviewed-plan authorization"
        )
    return generate_spark_world(
        world_seed,
        target_seed=derive_private_target_seed(config, world_seed),
    )


def _action_frame(action: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operation": action["operation"],
        "path": list(action["path"]),
        "binary_operator": action.get("binary_operator"),
        "motif_side": action.get("motif_side"),
    }


def _compact_action_row(
    action: EditAction,
    row: Mapping[str, Any],
    lineage: LineageRecord | None,
    world: SparkWorld,
) -> dict[str, Any]:
    constant: bool | None = None
    if lineage is not None:
        behavior = dsl.behavior_vector(lineage.child_ast, world.domain)
        constant = len(set(behavior)) <= 1
    flags = row.get("endpoint_flags")
    if not isinstance(flags, Mapping):
        raise UtilizationFeasibilityError("analyzed action endpoint flags are malformed")
    endpoint_flags = {name: bool(flags.get(name, False)) for name in ENDPOINT_NAMES}
    return {
        "raw_action_index": int(row["raw_action_index"]),
        "action": _action_frame(row["action"]),
        "action_hash": action.action_hash,
        "endpoint_flags": endpoint_flags,
        "child_behavior_hash": row.get("child_behavior_hash"),
        "child_behavior_is_constant": constant,
        "full_pool_counterfactual_bundle_sha256": row.get(
            "full_pool_counterfactual_bundle_sha256"
        ),
    }


def _context_profile(
    world: SparkWorld,
    motif: Mapping[str, Any],
    actions: Sequence[EditAction],
    lineages: Mapping[EditAction, LineageRecord],
    compressor: _BehaviorCachedCompressor,
    parent_result: Any,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for raw_index, action in enumerate(actions):
        row = _analyze_action(
            raw_action_index=raw_index,
            action=action,
            lineage=lineages.get(action),
            compressor=compressor,
            parent_result=parent_result,
            minimum_controls=3,
        )
        rows.append(_compact_action_row(action, row, lineages.get(action), world))
    flags_for = lambda endpoint: [
        row["raw_action_index"] for row in rows if row["endpoint_flags"][endpoint]
    ]
    k4 = flags_for("K4_full_pool")
    nonconstant = [
        row["raw_action_index"]
        for row in rows
        if row["endpoint_flags"]["K4_full_pool"]
        and row["child_behavior_is_constant"] is False
    ]
    constant = [
        row["raw_action_index"]
        for row in rows
        if row["endpoint_flags"]["K4_full_pool"]
        and row["child_behavior_is_constant"] is True
    ]
    return {
        "motif_id": motif["motif_id"],
        "motif_sexpr": motif["motif_sexpr"],
        "motif_canonical_hash": motif["motif_canonical_hash"],
        "motif_behavior_hash": motif["motif_behavior_hash"],
        "stratum": motif["stratum"],
        "complexity_bucket": list(motif["complexity_bucket"]),
        "raw_action_count": len(rows),
        "k1_raw_action_indices": flags_for("K1"),
        "k2_raw_action_indices": flags_for("K2"),
        "k3_raw_action_indices": flags_for("K3"),
        "k4_raw_action_indices": k4,
        "nonconstant_k4_raw_action_indices": nonconstant,
        "constant_k4_raw_action_indices": constant,
        "actions": rows,
    }


def _creation_census(profiles: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_stratum: dict[str, list[Mapping[str, Any]]] = {
        stratum: [] for stratum in spark_lineage.MOTIF_STRATA
    }
    for profile in profiles:
        by_stratum[str(profile["stratum"])].append(profile)
    output: dict[str, Any] = {"pair_count": 0, "by_stratum": {}}
    for stratum in spark_lineage.MOTIF_STRATA:
        rows = sorted(by_stratum[stratum], key=lambda row: str(row["motif_id"]))
        stats: dict[str, Any] = {"pair_count": 0}
        for endpoint_name in (
            "k1_raw_action_indices",
            "k2_raw_action_indices",
            "nonconstant_k4_raw_action_indices",
        ):
            stats[f"{endpoint_name}_equal_count"] = 0
            stats[f"{endpoint_name}_symmetric_difference_histogram"] = {}
        stats["constant_k4_count_histogram"] = {}
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                if left["motif_behavior_hash"] == right["motif_behavior_hash"]:
                    continue
                stats["pair_count"] += 1
                for field in (
                    "k1_raw_action_indices",
                    "k2_raw_action_indices",
                    "nonconstant_k4_raw_action_indices",
                ):
                    delta = len(set(left[field]).symmetric_difference(right[field]))
                    histogram = stats[f"{field}_symmetric_difference_histogram"]
                    histogram[str(delta)] = histogram.get(str(delta), 0) + 1
                    if delta == 0:
                        stats[f"{field}_equal_count"] += 1
                constant_total = len(left["constant_k4_raw_action_indices"]) + len(
                    right["constant_k4_raw_action_indices"]
                )
                histogram = stats["constant_k4_count_histogram"]
                histogram[str(constant_total)] = histogram.get(str(constant_total), 0) + 1
        output["by_stratum"][stratum] = stats
        output["pair_count"] += stats["pair_count"]
    return output


def _profile_action(profile: Mapping[str, Any], raw_index: int) -> Mapping[str, Any]:
    rows = [row for row in profile.get("actions", []) if row.get("raw_action_index") == raw_index]
    if len(rows) != 1:
        raise UtilizationFeasibilityError("profile raw action is absent or duplicated")
    return rows[0]


def _expected_action_frame(raw_index: int) -> dict[str, Any]:
    if type(raw_index) is not int or not 0 <= raw_index < RAW_ACTION_COUNT:
        raise UtilizationFeasibilityError("raw action index is malformed")
    path = [1, 1] if raw_index < 5 else [1, 2]
    frame = raw_index % 5
    if frame == 0:
        return {
            "operation": "replace",
            "path": path,
            "binary_operator": None,
            "motif_side": None,
        }
    operator, side = (
        ("add", "right"),
        ("sub", "right"),
        ("sub", "left"),
        ("mul", "right"),
    )[frame - 1]
    return {
        "operation": "wrap_binary",
        "path": path,
        "binary_operator": operator,
        "motif_side": side,
    }


def _validate_profiles(profiles: Sequence[Mapping[str, Any]]) -> None:
    expected_motifs = enumerate_full_motif_library()
    if len(profiles) != len(expected_motifs):
        raise UtilizationFeasibilityError("candidate world motif census is incomplete")
    for profile, motif in zip(profiles, expected_motifs, strict=True):
        if (
            not isinstance(profile, Mapping)
            or any(profile.get(field) != motif[field] for field in (
                "motif_id", "motif_sexpr", "motif_canonical_hash",
                "motif_behavior_hash", "stratum", "complexity_bucket",
            ))
            or profile.get("raw_action_count") != RAW_ACTION_COUNT
            or not isinstance(profile.get("actions"), list)
            or len(profile["actions"]) != RAW_ACTION_COUNT
            or [row.get("raw_action_index") for row in profile["actions"]]
            != list(range(RAW_ACTION_COUNT))
        ):
            raise UtilizationFeasibilityError("context profile motif identity/order drifted")
        seen: set[int] = set()
        expected_sets: dict[str, list[int]] = {endpoint: [] for endpoint in ENDPOINT_NAMES}
        nonconstant: list[int] = []
        constant: list[int] = []
        for raw_index, row in enumerate(profile["actions"]):
            if not isinstance(row, Mapping) or row.get("raw_action_index") in seen:
                raise UtilizationFeasibilityError("context action index is duplicated")
            seen.add(raw_index)
            if row.get("action") != _expected_action_frame(raw_index):
                raise UtilizationFeasibilityError("context action frame drifted")
            if not _is_sha256(row.get("action_hash")):
                raise UtilizationFeasibilityError("context action hash is malformed")
            flags = row.get("endpoint_flags")
            if (
                not isinstance(flags, Mapping)
                or set(flags) != set(ENDPOINT_NAMES)
                or any(type(flags[name]) is not bool for name in ENDPOINT_NAMES)
                or (flags["K2"] and not flags["K1"])
                or (flags["K3"] and not flags["K2"])
                or (flags["K4_full_pool"] and not flags["K3"])
            ):
                raise UtilizationFeasibilityError("context action endpoints are malformed")
            for endpoint in ENDPOINT_NAMES:
                if flags[endpoint]:
                    expected_sets[endpoint].append(raw_index)
            is_constant = row.get("child_behavior_is_constant")
            if flags["K1"]:
                if not _is_sha256(row.get("child_behavior_hash")) or type(is_constant) is not bool:
                    raise UtilizationFeasibilityError("K1 child identity is malformed")
                if not _is_sha256(row.get("full_pool_counterfactual_bundle_sha256")):
                    raise UtilizationFeasibilityError("K1 control bundle identity is malformed")
            elif any(
                row.get(field) is not None
                for field in (
                    "child_behavior_hash",
                    "child_behavior_is_constant",
                    "full_pool_counterfactual_bundle_sha256",
                )
            ):
                raise UtilizationFeasibilityError("K1-invalid action leaks child identity")
            if flags["K4_full_pool"]:
                if is_constant is True:
                    constant.append(raw_index)
                elif is_constant is False:
                    nonconstant.append(raw_index)
                else:
                    raise UtilizationFeasibilityError("K4 constant partition is malformed")
        if any(
            profile.get(f"{endpoint.lower()}_raw_action_indices")
            != expected_sets[endpoint]
            for endpoint in ("K1", "K2", "K3")
        ):
            raise UtilizationFeasibilityError("context endpoint action census drifted")
        if profile.get("k4_raw_action_indices") != expected_sets["K4_full_pool"]:
            raise UtilizationFeasibilityError("context K4 action census drifted")
        if (
            profile.get("nonconstant_k4_raw_action_indices") != nonconstant
            or profile.get("constant_k4_raw_action_indices") != constant
        ):
            raise UtilizationFeasibilityError("context K4 constant partition drifted")


def _pair_row(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    tier_id: str,
    required_nonconstant_count: int,
) -> dict[str, Any] | None:
    if tier_id not in TIER_IDS:
        raise UtilizationFeasibilityError("unknown utilization tier")
    if (
        left.get("stratum") != right.get("stratum")
        or left.get("complexity_bucket") != right.get("complexity_bucket")
        or left.get("motif_id") == right.get("motif_id")
        or left.get("motif_behavior_hash") == right.get("motif_behavior_hash")
    ):
        return None
    # K2 equality is a count equality, not a set equality.
    if len(left.get("k2_raw_action_indices", [])) != len(right.get("k2_raw_action_indices", [])):
        return None
    left_k4 = list(left.get("k4_raw_action_indices", []))
    right_k4 = list(right.get("k4_raw_action_indices", []))
    left_nonconstant = list(left.get("nonconstant_k4_raw_action_indices", []))
    right_nonconstant = list(right.get("nonconstant_k4_raw_action_indices", []))
    left_constant = list(left.get("constant_k4_raw_action_indices", []))
    right_constant = list(right.get("constant_k4_raw_action_indices", []))
    if (
        len(set(left_k4)) != len(left_k4)
        or len(set(right_k4)) != len(right_k4)
        or len(set(left_nonconstant)) != len(left_nonconstant)
        or len(set(right_nonconstant)) != len(right_nonconstant)
    ):
        return None
    if left_constant or right_constant:
        return None
    if len(left_k4) != len(left_nonconstant) or len(right_k4) != len(right_nonconstant):
        return None
    if (
        len(left_nonconstant) != required_nonconstant_count
        or len(right_nonconstant) != required_nonconstant_count
    ):
        return None
    if set(left_nonconstant).intersection(right_nonconstant):
        return None
    # Canonical context orientation makes pair rows independent of caller order.
    ordered = sorted((left, right), key=lambda row: str(row["motif_id"]))
    first, second = ordered
    first_raw = sorted(int(value) for value in first["nonconstant_k4_raw_action_indices"])
    second_raw = sorted(int(value) for value in second["nonconstant_k4_raw_action_indices"])
    first_actions = [_profile_action(first, raw) for raw in first_raw]
    second_actions = [_profile_action(second, raw) for raw in second_raw]
    return {
        "tier_id": tier_id,
        "stratum": first["stratum"],
        "complexity_bucket": list(first["complexity_bucket"]),
        "context_a_motif_id": first["motif_id"],
        "context_b_motif_id": second["motif_id"],
        "context_a_motif_behavior_hash": first["motif_behavior_hash"],
        "context_b_motif_behavior_hash": second["motif_behavior_hash"],
        "k2_opportunity_count": len(first["k2_raw_action_indices"]),
        "context_a_correct_raw_action_indices": first_raw,
        "context_b_correct_raw_action_indices": second_raw,
        "correct_raw_action_sets_disjoint": True,
        "context_a_correct_actions": [
            {
                "raw_action_index": row["raw_action_index"],
                "action": row["action"],
                "child_behavior_hash": row.get("child_behavior_hash"),
                "full_pool_counterfactual_bundle_sha256": row.get(
                    "full_pool_counterfactual_bundle_sha256"
                ),
            }
            for row in first_actions
        ],
        "context_b_correct_actions": [
            {
                "raw_action_index": row["raw_action_index"],
                "action": row["action"],
                "child_behavior_hash": row.get("child_behavior_hash"),
                "full_pool_counterfactual_bundle_sha256": row.get(
                    "full_pool_counterfactual_bundle_sha256"
                ),
            }
            for row in second_actions
        ],
    }


def strict_pair_predicate(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _pair_row(left, right, tier_id=STRICT_TIER, required_nonconstant_count=1) is not None


def degraded_pair_predicate(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return _pair_row(left, right, tier_id=DEGRADED_TIER, required_nonconstant_count=2) is not None


def classify_pair_tiers(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    return {
        STRICT_TIER: _pair_row(left, right, tier_id=STRICT_TIER, required_nonconstant_count=1),
        DEGRADED_TIER: _pair_row(left, right, tier_id=DEGRADED_TIER, required_nonconstant_count=2),
    }


def pair_candidates_for_world(
    profiles: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    ordered = sorted(profiles, key=lambda row: str(row["motif_id"]))
    result = {tier: [] for tier in TIER_IDS}
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            tiers = classify_pair_tiers(left, right)
            for tier in TIER_IDS:
                if tiers[tier] is not None:
                    result[tier].append(dict(tiers[tier]))
    return result


def _scan_candidate_world(
    config: Mapping[str, Any],
    plan_candidate: Mapping[str, Any],
    *,
    authorization: _ReviewedPlanAuthorization,
) -> dict[str, Any]:
    """Exhaust one target-conditioned world over all 105 motifs and ten actions."""

    if (
        type(plan_candidate.get("candidate_index")) is not int
        or type(plan_candidate.get("world_seed")) is not int
    ):
        raise UtilizationFeasibilityError("plan candidate is malformed")
    world_seed = int(plan_candidate["world_seed"])
    world = _materialize_private_candidate_world(
        config,
        world_seed,
        authorization=authorization,
    )
    parent = select_parent(world)
    compressor = _BehaviorCachedCompressor(SparkCompressor(world))
    parent_result = compressor.run(parent, max_rounds=spark_closure.CLOSURE_MAX_ROUNDS)
    lineages = _lineage_index(spark_lineage.enumerate_reachable_children(world))
    profiles: list[dict[str, Any]] = []
    for motif in enumerate_full_motif_library():
        actions = _raw_actions(world, str(motif["motif_id"]))
        profiles.append(
            _context_profile(world, motif, actions, lineages, compressor, parent_result)
        )
    pairs = pair_candidates_for_world(profiles)
    unsigned: dict[str, Any] = {
        "candidate_index": int(plan_candidate["candidate_index"]),
        "world_seed": world_seed,
        "target_seed_namespace_sha256": spark_closure._target_seed_digest(
            world_seed,
            namespace=str(config["development_target_materialization"]["target_seed_namespace"]),
        ),
        "target_index": world.target_index,
        "world_hash": world.world_hash,
        "target_canonical_hash": dsl.canonical_hash(world.target),
        "parent_canonical_hash": dsl.canonical_hash(parent),
        "target_materialized": True,
        "profiles": profiles,
        "creation_census": _creation_census(profiles),
        "pair_candidates": pairs,
        "compressor_cache": {"hits": compressor.cache_hits, "misses": compressor.cache_misses},
        "development_only": True,
        "model_outputs_read": False,
        "provider_calls_made": 0,
    }
    return {**unsigned, "candidate_world_sha256": _sha256_json(unsigned)}


def _validate_candidate_world(
    world: Mapping[str, Any],
    expected_candidate: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> None:
    unsigned = {key: value for key, value in world.items() if key != "candidate_world_sha256"}
    if (
        world.get("candidate_world_sha256") != _sha256_json(unsigned)
        or world.get("candidate_index") != expected_candidate.get("candidate_index")
        or world.get("world_seed") != expected_candidate.get("world_seed")
        or world.get("target_materialized") is not True
        or world.get("development_only") is not True
        or world.get("model_outputs_read") is not False
        or world.get("provider_calls_made") != 0
        or not _is_sha256(world.get("target_seed_namespace_sha256"))
        or type(world.get("target_index")) is not int
        or not 0 <= world.get("target_index", -1) < 256
        or not _is_sha256(world.get("world_hash"))
        or not _is_sha256(world.get("target_canonical_hash"))
        or not _is_sha256(world.get("parent_canonical_hash"))
        or not isinstance(world.get("profiles"), list)
        or len(world["profiles"]) != MOTIF_COUNT
        or not isinstance(world.get("pair_candidates"), Mapping)
    ):
        raise UtilizationFeasibilityError("candidate world is malformed or tampered")
    profiles = world["profiles"]
    _validate_profiles(profiles)
    if world.get("creation_census") != _creation_census(profiles):
        raise UtilizationFeasibilityError("candidate creation census drifted")
    if world.get("pair_candidates") != pair_candidates_for_world(profiles):
        raise UtilizationFeasibilityError("candidate pair geometry drifted")
    if config is not None:
        expected_namespace = str(
            config["development_target_materialization"]["target_seed_namespace"]
        )
        if world.get("target_seed_namespace_sha256") != spark_closure._target_seed_digest(
            int(world["world_seed"]), namespace=expected_namespace
        ):
            raise UtilizationFeasibilityError("candidate target namespace binding drifted")


def _matching_for_capacity(
    eligible_by_world: Mapping[int, Sequence[str]],
    *,
    strata: Sequence[str],
    capacity_per_stratum: int,
) -> dict[str, Any]:
    """Lexicographically first exact joint b-matching with world capacity one."""

    frozen_strata = tuple(strata)
    if len(set(frozen_strata)) != len(frozen_strata) or capacity_per_stratum < 1:
        raise UtilizationFeasibilityError("matching strata/capacity are malformed")
    order = {stratum: index for index, stratum in enumerate(frozen_strata)}
    normalized: dict[int, tuple[str, ...]] = {}
    for candidate, values in eligible_by_world.items():
        if type(candidate) is not int or candidate < 0:
            raise UtilizationFeasibilityError("matching candidate index is malformed")
        if not isinstance(values, (list, tuple, set)):
            raise UtilizationFeasibilityError("matching eligibility is malformed")
        if any(value not in order for value in values):
            raise UtilizationFeasibilityError("matching contains an unknown stratum")
        normalized[candidate] = tuple(sorted(set(values), key=order.__getitem__))
    required = capacity_per_stratum * len(frozen_strata)

    def feasible(fixed: Mapping[int, str], lower: Mapping[str, int]) -> bool:
        counts = {
            stratum: sum(value == stratum for value in fixed.values())
            for stratum in frozen_strata
        }
        if len(fixed) != len(set(fixed)) or any(
            value > capacity_per_stratum for value in counts.values()
        ):
            return False
        nodes = tuple(
            (stratum, position)
            for stratum in frozen_strata
            for position in range(capacity_per_stratum - counts[stratum])
        )
        owner: dict[tuple[str, int], int] = {}

        def augment(candidate: int, seen: set[tuple[str, int]]) -> bool:
            for stratum in normalized[candidate]:
                if candidate <= lower.get(stratum, -1):
                    continue
                for node in nodes:
                    if node[0] != stratum or node in seen:
                        continue
                    seen.add(node)
                    incumbent = owner.get(node)
                    if incumbent is None or augment(incumbent, seen):
                        owner[node] = candidate
                        return True
            return False

        for candidate in sorted(normalized):
            if candidate in fixed:
                continue
            augment(candidate, set())
            if len(owner) == len(nodes):
                return True
        return len(owner) == len(nodes)

    if not feasible({}, {}):
        return {
            "capacity_per_stratum": capacity_per_stratum,
            "required_world_count": required,
            "matched_world_count": 0,
            "complete": False,
            "counts_by_construction_stratum": {stratum: 0 for stratum in frozen_strata},
            "assignments": [],
            "lexicographic_assignment_vector": None,
        }
    fixed: dict[int, str] = {}
    vector: list[int] = []
    for stratum in frozen_strata:
        lower = -1
        for _ in range(capacity_per_stratum):
            for candidate in sorted(normalized):
                if candidate <= lower or candidate in fixed or stratum not in normalized[candidate]:
                    continue
                trial = {**fixed, candidate: stratum}
                if feasible(trial, {stratum: candidate}):
                    fixed[candidate] = stratum
                    vector.append(candidate)
                    lower = candidate
                    break
            else:
                raise UtilizationFeasibilityError("matching lost a feasible completion")
    assignments = [
        {
            "candidate_index": candidate,
            "construction_stratum": fixed[candidate],
            "eligible_focal_strata": list(normalized[candidate]),
        }
        for candidate in sorted(fixed)
    ]
    counts = {
        stratum: sum(row["construction_stratum"] == stratum for row in assignments)
        for stratum in frozen_strata
    }
    return {
        "capacity_per_stratum": capacity_per_stratum,
        "required_world_count": required,
        "matched_world_count": len(assignments),
        "complete": len(assignments) == required
        and all(value == capacity_per_stratum for value in counts.values()),
        "counts_by_construction_stratum": counts,
        "assignments": assignments,
        "lexicographic_assignment_vector": vector,
    }


def _pair_geometry(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    raw_counts = Counter()
    path_counts = Counter()
    frame_counts = Counter()
    eligible_worlds: set[int] = set()
    child_behaviors: set[str] = set()
    bundles: set[str] = set()
    by_stratum: dict[str, dict[str, Any]] = {}
    for row in rows:
        candidate_index = row.get("candidate_index")
        if type(candidate_index) is not int or candidate_index < 0:
            raise UtilizationFeasibilityError(
                "pair geometry requires a non-negative candidate index"
            )
        eligible_worlds.add(candidate_index)
        stratum = str(row["stratum"])
        stratum_row = by_stratum.setdefault(
            stratum,
            {
                "pair_candidate_count": 0,
                "eligible_worlds": set(),
                "worlds_by_unordered_correct_raw_set_pair": {},
                "pair_candidates_by_unordered_correct_raw_set_pair": Counter(),
                "correct_raw_action_marginals": Counter(),
                "correct_path_marginals": Counter(),
                "correct_frame_marginals": Counter(),
                "child_behaviors": set(),
                "bundles": set(),
            },
        )
        stratum_row["pair_candidate_count"] += 1
        stratum_row["eligible_worlds"].add(candidate_index)
        raw_pair = tuple(
            sorted(
                (
                    tuple(int(value) for value in row["context_a_correct_raw_action_indices"]),
                    tuple(int(value) for value in row["context_b_correct_raw_action_indices"]),
                )
            )
        )
        raw_pair_key = str(raw_pair)
        stratum_row["worlds_by_unordered_correct_raw_set_pair"].setdefault(
            raw_pair_key,
            set(),
        ).add(candidate_index)
        stratum_row["pair_candidates_by_unordered_correct_raw_set_pair"][
            raw_pair_key
        ] += 1
        for side in ("context_a_correct_actions", "context_b_correct_actions"):
            for action in row.get(side, []):
                raw_counts[str(action["raw_action_index"])] += 1
                stratum_row["correct_raw_action_marginals"][str(action["raw_action_index"])] += 1
                path = tuple(action["action"].get("path", []))
                path_counts[str(path)] += 1
                stratum_row["correct_path_marginals"][str(path)] += 1
                frame = (
                    action["action"].get("operation"),
                    action["action"].get("binary_operator"),
                    action["action"].get("motif_side"),
                )
                frame_counts[str(frame)] += 1
                stratum_row["correct_frame_marginals"][str(frame)] += 1
                if _is_sha256(action.get("child_behavior_hash")):
                    child_behaviors.add(str(action["child_behavior_hash"]))
                    stratum_row["child_behaviors"].add(str(action["child_behavior_hash"]))
                if _is_sha256(action.get("full_pool_counterfactual_bundle_sha256")):
                    bundles.add(str(action["full_pool_counterfactual_bundle_sha256"]))
                    stratum_row["bundles"].add(
                        str(action["full_pool_counterfactual_bundle_sha256"])
                    )
    normalized_by_stratum: dict[str, Any] = {}
    for stratum in sorted(by_stratum):
        row = by_stratum[stratum]
        normalized_by_stratum[stratum] = {
            "pair_candidate_count": row["pair_candidate_count"],
            "eligible_world_count": len(row["eligible_worlds"]),
            "unordered_correct_raw_set_pair_world_capacity": {
                key: len(world_indices)
                for key, world_indices in sorted(
                    row["worlds_by_unordered_correct_raw_set_pair"].items()
                )
            },
            "unordered_correct_raw_set_pair_candidate_count": dict(
                sorted(
                    row[
                        "pair_candidates_by_unordered_correct_raw_set_pair"
                    ].items()
                )
            ),
            "correct_raw_action_marginals": {
                str(index): row["correct_raw_action_marginals"].get(str(index), 0)
                for index in range(RAW_ACTION_COUNT)
            },
            "correct_path_marginals": dict(sorted(row["correct_path_marginals"].items())),
            "correct_frame_marginals": dict(sorted(row["correct_frame_marginals"].items())),
            "unique_correct_child_behavior_count": len(row["child_behaviors"]),
            "unique_correct_full_pool_bundle_count": len(row["bundles"]),
        }
    return {
        "pair_candidate_count": len(rows),
        "eligible_world_count": len(eligible_worlds),
        "correct_raw_action_marginals": {
            str(index): raw_counts.get(str(index), 0)
            for index in range(RAW_ACTION_COUNT)
        },
        "correct_path_marginals": dict(sorted(path_counts.items())),
        "correct_frame_marginals": dict(sorted(frame_counts.items())),
        "unique_correct_child_behavior_count": len(child_behaviors),
        "unique_correct_full_pool_bundle_count": len(bundles),
        "by_stratum": normalized_by_stratum,
    }


def deterministic_tier_matching(
    worlds: Sequence[Mapping[str, Any]],
    *,
    tier_id: str,
    strata: Sequence[str] = spark_lineage.MOTIF_STRATA,
    target_per_stratum: int = 8,
    fallback_per_stratum: int = 4,
) -> dict[str, Any]:
    """Match unique worlds to one construction stratum, q=8 else q=4."""

    if tier_id not in TIER_IDS:
        raise UtilizationFeasibilityError("unknown utilization tier")
    if (
        type(target_per_stratum) is not int
        or type(fallback_per_stratum) is not int
        or target_per_stratum < 1
        or not 1 <= fallback_per_stratum <= target_per_stratum
    ):
        raise UtilizationFeasibilityError("matching geometry landmarks are malformed")
    by_world: dict[int, list[Mapping[str, Any]]] = {}
    for world in worlds:
        index = int(world["candidate_index"])
        if index in by_world:
            raise UtilizationFeasibilityError("matching input duplicates a world")
        candidates = world.get("pair_candidates", {}).get(tier_id, [])
        if not isinstance(candidates, list):
            raise UtilizationFeasibilityError("world pair candidates are malformed")
        by_world[index] = list(candidates)
    eligible = {
        index: tuple(sorted({str(row["stratum"]) for row in rows}, key=tuple(strata).index))
        for index, rows in by_world.items()
    }
    full = _matching_for_capacity(eligible, strata=strata, capacity_per_stratum=target_per_stratum)
    maximum_q = target_per_stratum if full["complete"] else 0
    if not full["complete"]:
        for q in range(target_per_stratum - 1, 0, -1):
            candidate = _matching_for_capacity(
                eligible,
                strata=strata,
                capacity_per_stratum=q,
            )
            if candidate["complete"]:
                maximum_q = q
                break
    fallback = _matching_for_capacity(
        eligible,
        strata=strata,
        capacity_per_stratum=fallback_per_stratum,
    )
    selected = full if full["complete"] else fallback
    if full["complete"]:
        selection_mode = "target_q"
        selected_q: int | None = target_per_stratum
    elif fallback["complete"]:
        selection_mode = "fallback_q"
        selected_q = fallback_per_stratum
    else:
        selection_mode = "infeasible_under_cap"
        selected_q = None
    if full["complete"]:
        classification = (
            "strict_unique_switch_geometry_feasible"
            if tier_id == STRICT_TIER
            else "degraded_equal_two_choice_switch_geometry_feasible"
        )
    elif selected["complete"]:
        classification = (
            "strict_unique_switch_geometry_feasible"
            if tier_id == STRICT_TIER
            else "degraded_equal_two_choice_switch_geometry_feasible"
        )
    else:
        classification = (
            "strict_unique_switch_geometry_infeasible_under_cap"
            if tier_id == STRICT_TIER
            else "degraded_equal_two_choice_switch_geometry_infeasible_under_cap"
        )
    assignments = []
    for assignment in selected["assignments"]:
        index = int(assignment["candidate_index"])
        stratum = str(assignment["construction_stratum"])
        options = [row for row in by_world[index] if row["stratum"] == stratum]
        if not options:
            raise UtilizationFeasibilityError("matching assignment lost pair witness")
        pair = min(
            options,
            key=lambda row: (
                row["tier_id"],
                row["stratum"],
                row["context_a_motif_id"],
                row["context_b_motif_id"],
                tuple(row["context_a_correct_raw_action_indices"]),
                tuple(row["context_b_correct_raw_action_indices"]),
            ),
        )
        assignments.append({**assignment, "pair": pair})
    all_candidates = [
        {**row, "candidate_index": index}
        for index, rows in by_world.items()
        for row in rows
    ]
    selected_candidates = [
        {**assignment["pair"], "candidate_index": assignment["candidate_index"]}
        for assignment in assignments
    ]
    return {
        "tier_id": tier_id,
        "classification": classification,
        **{key: value for key, value in selected.items() if key != "assignments"},
        "assignments": assignments,
        "selection_mode": selection_mode,
        "target_q": target_per_stratum,
        "fallback_q": fallback_per_stratum,
        "selected_q": selected_q,
        "target_q_feasible": bool(full["complete"]),
        "fallback_q_feasible": bool(fallback["complete"]),
        "maximum_exact_stratum_balanced_q_up_to_target": maximum_q,
        "full_target_matching": full,
        "fallback_matching": fallback,
        "candidate_capacity_by_stratum": {
            stratum: sum(stratum in values for values in eligible.values()) for stratum in strata
        },
        "candidate_geometry": _pair_geometry(all_candidates),
        "selected_matching_geometry": _pair_geometry(selected_candidates),
        "tier_mixing_allowed": False,
    }


def deterministic_balanced_matching(
    worlds: Sequence[Mapping[str, Any]],
    *,
    tier_id: str = STRICT_TIER,
    strata: Sequence[str] = spark_lineage.MOTIF_STRATA,
    target_per_stratum: int = 8,
    fallback_per_stratum: int = 4,
) -> dict[str, Any]:
    return deterministic_tier_matching(
        worlds,
        tier_id=tier_id,
        strata=strata,
        target_per_stratum=target_per_stratum,
        fallback_per_stratum=fallback_per_stratum,
    )


def _aggregate_worlds(worlds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    census = {
        "pair_count": sum(int(world["creation_census"]["pair_count"]) for world in worlds),
        "by_stratum": {},
    }
    for stratum in spark_lineage.MOTIF_STRATA:
        rows = [world["creation_census"]["by_stratum"][stratum] for world in worlds]
        merged: dict[str, Any] = {"pair_count": sum(int(row["pair_count"]) for row in rows)}
        for field in rows[0] if rows else ():
            if field == "pair_count":
                continue
            if field.endswith("_equal_count"):
                merged[field] = sum(int(row[field]) for row in rows)
            elif field.endswith("_histogram"):
                counts = Counter()
                for row in rows:
                    counts.update({str(key): int(value) for key, value in row[field].items()})
                merged[field] = dict(sorted(counts.items(), key=lambda pair: int(pair[0])))
        census["by_stratum"][stratum] = merged
    return {"world_count": len(worlds), "creation_census": census}


def build_scan_shard(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    config_file_sha256: str,
    start_index: int,
    count: int,
    reviewed_plan_sha256: str,
    require_current_source: bool = True,
) -> dict[str, Any]:
    candidates = validate_scan_plan(
        config,
        plan,
        config_file_sha256=config_file_sha256,
        require_current_source=require_current_source,
    )
    if reviewed_plan_sha256 != plan.get("plan_sha256"):
        raise UtilizationFeasibilityError(
            "scan requires the exact independently reviewed plan_sha256"
        )
    authorization = _ReviewedPlanAuthorization(
        reviewed_plan_sha256,
        _REVIEWED_PLAN_GUARD,
    )
    if (
        type(start_index) is not int
        or type(count) is not int
        or count != SHARD_WORLD_COUNT
        or start_index < 0
        or start_index % SHARD_WORLD_COUNT
        or start_index + count > WORLD_COUNT
    ):
        raise UtilizationFeasibilityError("scan shard must be one aligned eight-world stage")
    worlds = [
        _scan_candidate_world(
            config,
            candidates[index],
            authorization=authorization,
        )
        for index in range(start_index, start_index + count)
    ]
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": SHARD_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": config["evidence_scope"],
        "config_file_sha256": config_file_sha256,
        "plan_sha256": plan["plan_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "candidate_range": {
            "start": start_index,
            "count": count,
            "end_exclusive": start_index + count,
        },
        "aggregate": _aggregate_worlds(worlds),
        "worlds": worlds,
        "development_only": True,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "outcome_conditioned_benchmark_construction": True,
    }
    return {**unsigned, "shard_sha256": _sha256_json(unsigned)}


def _validate_shard(
    shard: Mapping[str, Any],
    *,
    config_file_sha256: str,
    plan: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
) -> tuple[int, int, list[Mapping[str, Any]]]:
    unsigned = {key: value for key, value in shard.items() if key != "shard_sha256"}
    candidate_range = shard.get("candidate_range")
    worlds = shard.get("worlds")
    if (
        shard.get("schema_version") != SCHEMA_VERSION
        or shard.get("kind") != SHARD_KIND
        or shard.get("protocol_id") != PROTOCOL_ID
        or shard.get("config_file_sha256") != config_file_sha256
        or shard.get("plan_sha256") != plan.get("plan_sha256")
        or shard.get("source_manifest_sha256") != plan.get("source_manifest_sha256")
        or shard.get("shard_sha256") != _sha256_json(unsigned)
        or shard.get("development_only") is not True
        or shard.get("model_outputs_read") is not False
        or shard.get("provider_calls_made") != 0
        or shard.get("outcome_conditioned_benchmark_construction") is not True
        or not isinstance(candidate_range, Mapping)
        or not isinstance(worlds, list)
    ):
        raise UtilizationFeasibilityError("scan shard is malformed or tampered")
    if config is not None and shard.get("evidence_scope") != config.get("evidence_scope"):
        raise UtilizationFeasibilityError("scan shard evidence scope drifted")
    start = candidate_range.get("start")
    count = candidate_range.get("count")
    end = candidate_range.get("end_exclusive")
    if (
        type(start) is not int
        or type(count) is not int
        or type(end) is not int
        or start < 0
        or count != SHARD_WORLD_COUNT
        or end != start + count
        or end > WORLD_COUNT
        or start % SHARD_WORLD_COUNT
        or len(worlds) != count
    ):
        raise UtilizationFeasibilityError("scan shard range is malformed")
    candidates = plan.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != WORLD_COUNT:
        raise UtilizationFeasibilityError("plan candidates are malformed")
    if [
        world.get("candidate_index")
        for world in worlds
        if isinstance(world, Mapping)
    ] != list(range(start, end)):
        raise UtilizationFeasibilityError("scan shard world order drifted")
    for world in worlds:
        if not isinstance(world, Mapping):
            raise UtilizationFeasibilityError("scan world is malformed")
        index = int(world["candidate_index"])
        _validate_candidate_world(world, candidates[index], config)
    if shard.get("aggregate") != _aggregate_worlds(worlds):
        raise UtilizationFeasibilityError("scan shard aggregate drifted")
    return int(start), int(end), worlds


def merge_scan_shards(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    shards: Sequence[Mapping[str, Any]],
    *,
    config_file_sha256: str,
    require_current_source: bool = True,
) -> dict[str, Any]:
    candidates = validate_scan_plan(
        config,
        plan,
        config_file_sha256=config_file_sha256,
        require_current_source=require_current_source,
    )
    validated = [
        _validate_shard(
            shard,
            config_file_sha256=config_file_sha256,
            plan=plan,
            config=config,
        )
        for shard in shards
    ]
    validated.sort(key=lambda item: item[0])
    cursor = 0
    worlds: list[Mapping[str, Any]] = []
    ranges: list[dict[str, int]] = []
    for start, end, rows in validated:
        if start != cursor:
            raise UtilizationFeasibilityError(
                f"scan shard ranges contain a {'overlap' if start < cursor else 'gap'}"
            )
        worlds.extend(rows)
        ranges.append({"start": start, "end_exclusive": end})
        cursor = end
    if cursor != WORLD_COUNT or len(worlds) != len(candidates):
        raise UtilizationFeasibilityError(
            "scan_incomplete_not_infeasible: merge requires complete fixed "
            "1024-world scan"
        )
    if [int(world["candidate_index"]) for world in worlds] != list(range(WORLD_COUNT)):
        raise UtilizationFeasibilityError("merged world order drifted")
    pair_worlds = [
        {
            "candidate_index": world["candidate_index"],
            "pair_candidates": world["pair_candidates"],
        }
        for world in worlds
    ]
    tier_results = {
        tier: deterministic_tier_matching(pair_worlds, tier_id=tier)
        for tier in TIER_IDS
    }
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": MERGED_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": config["evidence_scope"],
        "config_file_sha256": config_file_sha256,
        "plan_sha256": plan["plan_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "complete_candidate_range": {
            "start": 0,
            "count": WORLD_COUNT,
            "end_exclusive": WORLD_COUNT,
        },
        "shard_ranges": ranges,
        "aggregate": _aggregate_worlds(worlds),
        "tiers": tier_results,
        "worlds": worlds,
        "development_only": True,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "outcome_conditioned_benchmark_construction": True,
        "final_benchmark_minted": False,
    }
    return {**unsigned, "scan_sha256": _sha256_json(unsigned)}


def _read_json_bytes(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise UtilizationFeasibilityError(f"cannot read JSON input {path}") from exc
    if not isinstance(value, dict):
        raise UtilizationFeasibilityError(f"JSON input must be an object: {path}")
    return value, _sha256_bytes(payload)


def _emit_json_exclusive_0600(value: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    )
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise UtilizationFeasibilityError(f"refusing to overwrite artifact {output}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(output.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline strong-K4 utilization feasibility scan")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--config", type=Path, required=True)
    plan_parser.add_argument(
        "--reservation",
        "--seeds",
        "--seed-reservation",
        dest="reservation",
        type=Path,
        required=True,
    )
    plan_parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    plan_parser.add_argument("--output", type=Path, required=True)
    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--config", type=Path, required=True)
    scan_parser.add_argument("--plan", type=Path, required=True)
    scan_parser.add_argument("--shard-index", type=int)
    scan_parser.add_argument("--start-index", type=int)
    scan_parser.add_argument("--count", type=int)
    scan_parser.add_argument("--reviewed-plan-sha256", required=True)
    scan_parser.add_argument("--output", type=Path, required=True)
    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--config", type=Path, required=True)
    merge_parser.add_argument("--plan", type=Path, required=True)
    merge_parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    config, config_sha = _read_json_bytes(args.config)
    if args.command == "plan":
        reservation, reservation_sha = _read_json_bytes(args.reservation)
        registry, registry_sha = _read_json_bytes(args.registry)
        result = build_target_free_scan_plan(
            config,
            reservation,
            config_file_sha256=config_sha,
            seed_reservation_file_sha256=reservation_sha,
            source_manifest_sha256=_current_source_manifest_sha256(),
            registry=registry,
            registry_file_sha256=registry_sha,
        )
    else:
        plan, _ = _read_json_bytes(args.plan)
        if args.command == "scan":
            stage = SHARD_WORLD_COUNT
            if args.shard_index is not None:
                if (
                    args.start_index is not None
                    or args.count is not None
                    or not 0 <= args.shard_index < WORLD_COUNT // stage
                ):
                    raise UtilizationFeasibilityError(
                        "shard index must be in 0..127 and cannot mix range flags"
                    )
                start_index, count = args.shard_index * stage, stage
            elif args.start_index is not None and args.count is not None:
                start_index, count = args.start_index, args.count
            else:
                raise UtilizationFeasibilityError("scan requires shard index or start/count")
            result = build_scan_shard(
                config,
                plan,
                config_file_sha256=config_sha,
                start_index=start_index,
                count=count,
                reviewed_plan_sha256=args.reviewed_plan_sha256,
            )
        else:
            shards = [_read_json_bytes(path)[0] for path in args.inputs]
            result = merge_scan_shards(config, plan, shards, config_file_sha256=config_sha)
    _emit_json_exclusive_0600(result, args.output)
    return 0


__all__ = [
    "CONFIG_KIND", "DEGRADED_TIER", "ENDPOINT_NAMES", "MERGED_KIND", "MOTIF_COUNT",
    "PLAN_KIND", "PROTOCOL_ID", "RAW_ACTION_COUNT", "SHARD_KIND", "SHARD_WORLD_COUNT",
    "STRICT_TIER", "TIER_IDS", "UtilizationFeasibilityError", "build_scan_shard",
    "build_target_free_scan_plan", "classify_pair_tiers", "degraded_pair_predicate",
    "derive_candidate_seed_vector", "derive_candidate_world_seed", "derive_private_target_seed",
    "deterministic_balanced_matching",
    "deterministic_tier_matching",
    "enumerate_full_motif_library",
    "full_motif_library", "main", "merge_scan_shards",
    "motif_library_identity",
    "pair_candidates_for_world",
    "strict_pair_predicate",
    "validate_config", "validate_scan_plan", "validate_seed_reservation",
]


if __name__ == "__main__":
    raise SystemExit(main())
