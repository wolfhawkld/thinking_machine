"""Target-sealed, offline feasibility scan for the strong full-pool K4 gate.

This module constructs an *outcome-conditioned benchmark candidate set*.  It
does not call a model, read model output, or alter the historical formal K4
endpoint.  The workflow is deliberately split in three:

``plan``
    Freeze all 1,024 candidate seeds and their three target-blind motif slots.
``scan``
    Open one frozen 64-world shard and exhaust its 10 actions per slot.
``merge``
    Validate the complete contiguous scan and perform deterministic balanced
    world matching.

K1, K2, and K3 retain the four-round layered definitions.  The new endpoint is
named ``K4_full_pool``: K3 must hold, there must be at least three distinct
complete-domain replacement behaviours, and every such control must fail
exact identification.  Behaviour-equivalent controls are never counted twice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import dsl, spark_closure, spark_lineage
from .provenance import PROJECT_ROOT, source_manifest
from .spark_compressor import CompressionResult, SparkCompressor
from .spark_lineage import EditAction, LineageRecord, motif_by_id, select_parent
from .spark_world import SparkWorld, generate_spark_world


SCHEMA_VERSION = 1
PROTOCOL_ID = "spark-strong-k4-feasibility-v2"
CONFIG_KIND = "spark-strong-k4-feasibility-scan-config"
PLAN_KIND = "spark-strong-k4-feasibility-scan-plan"
SHARD_KIND = "spark-strong-k4-feasibility-scan-shard"
MERGED_KIND = "spark-strong-k4-feasibility-scan-result"
ENDPOINT_NAMES = ("K1", "K2", "K3", "K4_full_pool")
SEED_MASK_63 = (1 << 63) - 1
DEFAULT_REGISTRY_PATH = PROJECT_ROOT / "configs" / "development-seed-registry.json"
INTEGRITY_SCOPE = (
    "canonical self-digests and bound config/plan/source identities detect accidental "
    "drift; they are not signatures and do not prove execution authenticity"
)


class StrongK4ScanError(ValueError):
    """Raised when a frozen scan input or deterministic result fails closed."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def derive_candidate_world_seed(namespace: str, candidate_index: int) -> int:
    """Derive one frozen 63-bit candidate seed from its original index."""

    if not isinstance(namespace, str) or not namespace:
        raise StrongK4ScanError("world-seed namespace must be non-empty")
    if type(candidate_index) is not int or candidate_index < 0:
        raise StrongK4ScanError("candidate index must be a non-negative integer")
    digest = hashlib.sha256(
        f"{namespace}:{candidate_index}".encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], "big") & SEED_MASK_63


def derive_candidate_seed_vector(config: Mapping[str, Any]) -> tuple[int, ...]:
    stream = config.get("candidate_stream")
    if not isinstance(stream, Mapping):
        raise StrongK4ScanError("candidate_stream is malformed")
    start = stream.get("candidate_index_start")
    count = stream.get("candidate_world_count")
    namespace = stream.get("world_seed_namespace")
    if type(start) is not int or type(count) is not int or count <= 0:
        raise StrongK4ScanError("candidate range is malformed")
    if not isinstance(namespace, str):
        raise StrongK4ScanError("candidate namespace is malformed")
    return tuple(
        derive_candidate_world_seed(namespace, index)
        for index in range(start, start + count)
    )


def validate_config(config: Mapping[str, Any]) -> None:
    """Validate the scientific constants, not merely the JSON shape."""

    stream = config.get("candidate_stream")
    namespaces = config.get("private_target_and_public_motif_namespaces")
    grid = config.get("world_grid")
    endpoint = config.get("endpoint")
    cohort = config.get("balanced_cohort")
    artifact = config.get("artifact_contract")
    if not all(
        isinstance(value, Mapping)
        for value in (stream, namespaces, grid, endpoint, cohort, artifact)
    ):
        raise StrongK4ScanError("strong-K4 config sections are malformed")
    assert isinstance(stream, Mapping)
    assert isinstance(namespaces, Mapping)
    assert isinstance(grid, Mapping)
    assert isinstance(endpoint, Mapping)
    assert isinstance(cohort, Mapping)
    assert isinstance(artifact, Mapping)
    expected_pairs = (
        (config.get("schema_version"), SCHEMA_VERSION),
        (config.get("kind"), CONFIG_KIND),
        (config.get("protocol_id"), PROTOCOL_ID),
        (stream.get("candidate_index_start"), 0),
        (stream.get("candidate_world_count"), 1024),
        (stream.get("fixed_full_scan_required"), True),
        (stream.get("stage_world_count"), 64),
        (stream.get("world_seed_namespace"), f"{PROTOCOL_ID}:world-seed"),
        (grid.get("assigned_motif_slots_per_world"), 3),
        (grid.get("raw_actions_per_slot"), 10),
        (grid.get("action_paths"), [[1, 1], [1, 2]]),
        (
            grid.get("action_frames_per_path"),
            ["replace", "add-right", "sub-right", "sub-left", "mul-right"],
        ),
        (grid.get("motif_strata_in_frozen_order"), list(spark_lineage.MOTIF_STRATA)),
        (grid.get("target_draws_per_world"), 1),
        (grid.get("motif_redraws_per_slot"), 0),
        (grid.get("candidate_replacement_on_outcome"), False),
        (endpoint.get("name"), "K4_full_pool"),
        (endpoint.get("historical_formal_K4_reclassified"), False),
        (endpoint.get("closure_max_rounds"), spark_closure.CLOSURE_MAX_ROUNDS),
        (endpoint.get("requires_K3"), True),
        (endpoint.get("minimum_unique_control_behaviors"), 3),
        (endpoint.get("all_unique_controls_must_fail_exact_identification"), True),
        (endpoint.get("behavior_equivalent_controls_must_have_identical_endpoint"), True),
        (cohort.get("target_worlds_per_stratum"), 8),
        (cohort.get("target_total_worlds"), 32),
        (cohort.get("fallback_worlds_per_stratum"), 6),
        (cohort.get("fallback_total_worlds"), 24),
        (cohort.get("independent_unit"), "unique world"),
        (cohort.get("one_construction_stratum_assignment_per_world"), True),
        (cohort.get("full_classification"), "full_32_balanced_feasible"),
        (cohort.get("fallback_classification"), "reduced_24_balanced_feasible"),
        (
            cohort.get("failure_classification"),
            "balanced_strong_K4_benchmark_not_feasible_under_cap",
        ),
        (cohort.get("fixed_scan_then_select"), True),
        (artifact.get("model_outputs_read"), False),
        (artifact.get("provider_calls_made"), 0),
    )
    if any(actual != expected for actual, expected in expected_pairs):
        raise StrongK4ScanError("strong-K4 config constants drifted")
    if namespaces.get("target_seed_namespace") != PROTOCOL_ID or namespaces.get(
        "motif_selection_namespace"
    ) != PROTOCOL_ID:
        raise StrongK4ScanError("target or motif namespace drifted")
    if namespaces.get("target_seed_rule") != (
        "sha256(target_seed_namespace + ':target:' + decimal_world_seed), full "
        "32-byte digest interpreted as one big-endian integer"
    ) or namespaces.get("target_selection_rule") != (
        "call existing generate_spark_world(world_seed, target_seed) exactly once; "
        "its existing random.Random(target_seed).randrange(256) selects the sole target"
    ):
        raise StrongK4ScanError("private target derivation rule drifted")
    public_exclusions = artifact.get("future_public_projection_excludes")
    if (
        not isinstance(public_exclusions, list)
        or len(public_exclusions) != len(_PUBLIC_FORBIDDEN_KEYS)
        or set(public_exclusions) != _PUBLIC_FORBIDDEN_KEYS
    ):
        raise StrongK4ScanError("future public/private projection boundary drifted")
    seeds = derive_candidate_seed_vector(config)
    if len(set(seeds)) != 1024:
        raise StrongK4ScanError("candidate seed vector contains a collision")
    encoded_vector = json.dumps(seeds, separators=(",", ":")).encode("ascii")
    if stream.get("candidate_seed_vector_sha256") != _sha256_bytes(encoded_vector):
        raise StrongK4ScanError("candidate seed vector digest drifted")


def validate_seed_registry(
    config: Mapping[str, Any], registry: Mapping[str, Any]
) -> dict[str, Any]:
    """Prove the exact vector is the registry suffix and has no old collision."""

    validate_config(config)
    registered = registry.get("seeds")
    records = registry.get("records")
    if not isinstance(registered, list) or not isinstance(records, list):
        raise StrongK4ScanError("development seed registry is malformed")
    vector = list(derive_candidate_seed_vector(config))
    if len(registered) < len(vector) or registered[-len(vector) :] != vector:
        raise StrongK4ScanError("registry suffix is not the exact candidate vector")
    historical = registered[: -len(vector)]
    if any(type(seed) is not int for seed in registered):
        raise StrongK4ScanError("registry contains a non-integer seed")
    if set(historical).intersection(vector):
        raise StrongK4ScanError("candidate seed collides with a historical seed")
    if len(set(vector)) != len(vector):
        raise StrongK4ScanError("candidate seed vector contains duplicates")
    last = records[-1] if records else None
    stream = config["candidate_stream"]
    last_draw = last.get("draw") if isinstance(last, Mapping) else None
    if not isinstance(last, Mapping) or (
        last.get("candidate_indices")
        != {"start": 0, "count": len(vector)}
        or last.get("candidate_seed_vector_sha256")
        != stream["candidate_seed_vector_sha256"]
        or not isinstance(last_draw, Mapping)
        or last_draw.get("namespace")
        != stream["world_seed_namespace"]
    ):
        raise StrongK4ScanError("registry's final reservation record drifted")
    return {
        "candidate_seed_count": len(vector),
        "candidate_seed_vector_sha256": stream["candidate_seed_vector_sha256"],
        "registry_suffix_exact": True,
        "historical_collision_count": 0,
    }


def _current_source_manifest_sha256() -> str:
    value = source_manifest(PROJECT_ROOT).get("source_manifest_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise StrongK4ScanError("current source manifest is malformed")
    return value


def _assert_current_source_manifest(expected: str) -> None:
    if _current_source_manifest_sha256() != expected:
        raise StrongK4ScanError("source manifest drifted")


def _frozen_slots_for_candidate(
    config: Mapping[str, Any], candidate_index: int, world_seed: int
) -> list[dict[str, Any]]:
    namespace = config["private_target_and_public_motif_namespaces"][
        "motif_selection_namespace"
    ]
    rows: list[dict[str, Any]] = []
    for factual_index in range(3):
        slot_index = factual_index + 1
        stratum = spark_closure._stratum_for(
            candidate_index,
            factual_index,
            factual_calls_per_world=3,
        )
        motif, selection_digest = spark_closure._select_motif(
            world_seed,
            slot_index,
            stratum,
            namespace=namespace,
        )
        rows.append(
            {
                "slot_id": f"candidate-{candidate_index}:motif-{slot_index}",
                "candidate_index": candidate_index,
                "world_seed": world_seed,
                "slot_index": slot_index,
                "condition": "motif",
                "motif_id": motif.motif_id,
                "motif_stratum": motif.stratum,
                "motif": dsl.to_sexpr(motif.ast),
                "motif_selection_sha256": selection_digest,
            }
        )
    return rows


def build_target_free_scan_plan(
    config: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    config_file_sha256: str,
    registry_file_sha256: str,
    source_manifest_sha256: str,
) -> dict[str, Any]:
    """Freeze seeds and motif assignments without constructing any world target."""

    validate_config(config)
    registry_audit = validate_seed_registry(config, registry)
    for name, digest in (
        ("config", config_file_sha256),
        ("registry", registry_file_sha256),
        ("source manifest", source_manifest_sha256),
    ):
        if not isinstance(digest, str) or len(digest) != 64:
            raise StrongK4ScanError(f"{name} digest is malformed")
    stream = config["candidate_stream"]
    start = int(stream["candidate_index_start"])
    seeds = derive_candidate_seed_vector(config)
    candidates = [
        {
            "candidate_index": candidate_index,
            "world_seed": world_seed,
            "slots": _frozen_slots_for_candidate(
                config, candidate_index, world_seed
            ),
        }
        for candidate_index, world_seed in zip(
            range(start, start + len(seeds)), seeds, strict=True
        )
    ]
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": config["evidence_scope"],
        "config_file_sha256": config_file_sha256,
        "seed_registry_file_sha256": registry_file_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "candidate_seed_vector_sha256": stream["candidate_seed_vector_sha256"],
        "registry_audit": registry_audit,
        "candidate_range": {
            "start": start,
            "count": len(seeds),
            "end_exclusive": start + len(seeds),
        },
        "candidates": candidates,
        "target_materialized": False,
        "compressor_run": False,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "outcome_conditioned_benchmark_construction": False,
        "integrity_scope": INTEGRITY_SCOPE,
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
    expected_keys = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence_scope",
        "config_file_sha256",
        "seed_registry_file_sha256",
        "source_manifest_sha256",
        "candidate_seed_vector_sha256",
        "registry_audit",
        "candidate_range",
        "candidates",
        "target_materialized",
        "compressor_run",
        "model_outputs_read",
        "provider_calls_made",
        "outcome_conditioned_benchmark_construction",
        "integrity_scope",
        "plan_sha256",
    }
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    candidates = plan.get("candidates")
    if (
        set(plan) != expected_keys
        or plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("kind") != PLAN_KIND
        or plan.get("protocol_id") != PROTOCOL_ID
        or plan.get("evidence_scope") != config.get("evidence_scope")
        or plan.get("config_file_sha256") != config_file_sha256
        or not isinstance(plan.get("seed_registry_file_sha256"), str)
        or len(plan["seed_registry_file_sha256"]) != 64
        or plan.get("candidate_seed_vector_sha256")
        != config["candidate_stream"]["candidate_seed_vector_sha256"]
        or plan.get("candidate_range")
        != {"start": 0, "count": 1024, "end_exclusive": 1024}
        or plan.get("registry_audit")
        != {
            "candidate_seed_count": 1024,
            "candidate_seed_vector_sha256": config["candidate_stream"]
            ["candidate_seed_vector_sha256"],
            "registry_suffix_exact": True,
            "historical_collision_count": 0,
        }
        or plan.get("plan_sha256") != _sha256_json(unsigned)
        or plan.get("target_materialized") is not False
        or plan.get("compressor_run") is not False
        or plan.get("model_outputs_read") is not False
        or plan.get("provider_calls_made") != 0
        or plan.get("outcome_conditioned_benchmark_construction") is not False
        or plan.get("integrity_scope") != INTEGRITY_SCOPE
        or not isinstance(candidates, list)
        or len(candidates) != 1024
    ):
        raise StrongK4ScanError("target-free scan plan is malformed or tampered")
    expected_seeds = derive_candidate_seed_vector(config)
    validated: list[Mapping[str, Any]] = []
    for candidate_index, (candidate, seed) in enumerate(
        zip(candidates, expected_seeds, strict=True)
    ):
        if not isinstance(candidate, Mapping):
            raise StrongK4ScanError("plan candidate is malformed")
        expected_slots = _frozen_slots_for_candidate(config, candidate_index, seed)
        if candidate != {
            "candidate_index": candidate_index,
            "world_seed": seed,
            "slots": expected_slots,
        }:
            raise StrongK4ScanError("plan candidate seed or motif assignment drifted")
        validated.append(candidate)
    manifest = plan.get("source_manifest_sha256")
    if not isinstance(manifest, str) or len(manifest) != 64:
        raise StrongK4ScanError("plan source manifest is malformed")
    if require_current_source:
        _assert_current_source_manifest(manifest)
    return tuple(validated)


class _BehaviorCachedCompressor:
    """Cache a world's deterministic four-round result by complete behavior."""

    def __init__(self, compressor: SparkCompressor) -> None:
        self._compressor = compressor
        self._cache: dict[tuple[int, str], CompressionResult] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._compressor, name)

    def run(self, ast: dsl.Expr, *, max_rounds: int) -> CompressionResult:
        canonical = dsl.canonicalize(ast)
        canonical_hash = dsl.canonical_hash(canonical)
        behavior_hash = dsl.behavior_hash(canonical, self.domain)
        key = (max_rounds, behavior_hash)
        result = self._cache.get(key)
        if result is None:
            result = self._compressor.run(canonical, max_rounds=max_rounds)
            self._cache[key] = result
            self.cache_misses += 1
        else:
            self.cache_hits += 1
        if result.seed_canonical_hash == canonical_hash:
            return result
        return replace(
            result,
            seed_ast=canonical,  # type: ignore[arg-type]
            seed_canonical_hash=canonical_hash,
        )


def _trajectory(result: CompressionResult) -> dict[str, Any]:
    return {
        "N_t": list(result.N_t),
        "N_T": result.N_T,
        "rounds_completed": result.rounds_completed,
        "truth_retained": result.truth_retained,
        "full_domain_recovered": result.full_domain_recovered,
        "exact_identification": result.exact_identification,
        "positive_non_match_contraction": any(
            not step.response.is_match and step.N_after < step.N_before
            for step in result.steps
        ),
    }


def _is_k2(child_result: CompressionResult, *, child_direct_hit: bool) -> bool:
    return bool(
        not child_direct_hit
        and child_result.truth_retained
        and child_result.N_T == 1
        and child_result.full_domain_recovered
        and any(
            not step.response.is_match and step.N_after < step.N_before
            for step in child_result.steps
        )
        and child_result.rounds_completed <= spark_closure.CLOSURE_MAX_ROUNDS
    )


def classify_full_pool_controls(
    *,
    k3: bool,
    control_outcomes: Sequence[Mapping[str, Any]],
    minimum_unique_control_behaviors: int = 3,
) -> dict[str, Any]:
    """Evaluate the strong gate, failing closed on equivalent-control conflict."""

    if type(k3) is not bool or (
        type(minimum_unique_control_behaviors) is not int
        or minimum_unique_control_behaviors < 1
    ):
        raise StrongK4ScanError("strong full-pool predicate arguments are malformed")
    grouped: dict[str, list[tuple[bool, int]]] = {}
    for row in control_outcomes:
        behavior = row.get("child_behavior_hash")
        exact = row.get("exact_identification")
        n_t = row.get("N_T")
        if (
            not isinstance(behavior, str)
            or len(behavior) != 64
            or type(exact) is not bool
            or type(n_t) is not int
            or n_t < 1
        ):
            raise StrongK4ScanError("full-pool control outcome is malformed")
        grouped.setdefault(behavior, []).append((exact, n_t))
    conflicts = sorted(
        behavior
        for behavior, values in grouped.items()
        if len({exact for exact, _n_t in values}) != 1
        or len({_n_t for _exact, _n_t in values}) != 1
    )
    if conflicts:
        raise StrongK4ScanError(
            "behavior-equivalent controls have conflicting deterministic outcomes"
        )
    unique_rows = [
        {
            "child_behavior_hash": behavior,
            "exact_identification": grouped[behavior][0][0],
            "N_T": grouped[behavior][0][1],
            "equivalent_syntax_count": len(grouped[behavior]),
        }
        for behavior in sorted(grouped)
    ]
    enough = len(grouped) >= minimum_unique_control_behaviors
    all_fail = bool(unique_rows) and not any(
        row["exact_identification"] for row in unique_rows
    )
    passed = bool(k3 and enough and all_fail)
    return {
        "K4_full_pool": passed,
        "requires_K3_passed": k3,
        "minimum_unique_control_behaviors": minimum_unique_control_behaviors,
        "unique_control_behavior_count": len(grouped),
        "minimum_unique_controls_passed": enough,
        "all_unique_controls_fail_exact_identification": all_fail,
        "behavior_equivalence_conflict": False,
        "conflicting_behavior_hashes": [],
        "unique_control_outcomes": unique_rows,
    }


def _raw_actions(world: SparkWorld, motif_id: str) -> tuple[EditAction, ...]:
    parent = select_parent(world)
    motif = motif_by_id(motif_id)
    actions = tuple(spark_lineage._candidate_action_variants(parent, motif))
    if len(actions) != 10 or len(set(actions)) != 10:
        raise StrongK4ScanError("raw action grammar is not exactly ten actions")
    return actions


def _action_dict(action: EditAction) -> dict[str, Any]:
    return {
        "operation": action.operation,
        "path": list(action.path),
        "binary_operator": action.binary_operator,
        "motif_side": action.motif_side,
    }


def _frame_dict(lineage: LineageRecord) -> dict[str, Any]:
    return {
        "operation": lineage.action.operation,
        "path": list(lineage.action.path),
        "expected_old_subtree_hash": lineage.action.expected_old_subtree_hash,
        "binary_operator": lineage.action.binary_operator,
        "motif_side": lineage.action.motif_side,
        "motif_stratum": lineage.motif_stratum,
        "motif_complexity_bucket": list(lineage.motif_complexity_bucket),
    }


def _unique_replacement_groups(
    lineage: LineageRecord,
) -> list[tuple[str, tuple[Any, ...]]]:
    grouped: dict[str, list[Any]] = {}
    for replacement in lineage.matched_replacements:
        if (
            replacement.motif_stratum != lineage.motif_stratum
            or motif_by_id(replacement.motif_id).complexity_bucket
            != lineage.motif_complexity_bucket
            or replacement.child_behavior_hash == lineage.child_behavior_hash
        ):
            raise StrongK4ScanError("matched replacement pool violates its frame")
        grouped.setdefault(replacement.child_behavior_hash, []).append(replacement)
    return [
        (
            behavior,
            tuple(
                sorted(
                    grouped[behavior],
                    key=lambda row: (
                        row.child_canonical_hash,
                        row.motif_id,
                    ),
                )
            ),
        )
        for behavior in sorted(grouped)
    ]


def _full_pool_bundle(lineage: LineageRecord) -> tuple[str, dict[str, Any]]:
    control_hashes = [behavior for behavior, _rows in _unique_replacement_groups(lineage)]
    payload = {
        "frame": _frame_dict(lineage),
        "focal_child_behavior_hash": lineage.child_behavior_hash,
        "sorted_unique_control_behavior_hashes": control_hashes,
    }
    return _sha256_json(payload), payload


def _lineage_index(lineages: Sequence[LineageRecord]) -> dict[EditAction, LineageRecord]:
    result: dict[EditAction, LineageRecord] = {}
    for lineage in lineages:
        if lineage.action in result:
            raise StrongK4ScanError("one action has multiple control-ready lineages")
        result[lineage.action] = lineage
    return result


def _analyze_action(
    *,
    raw_action_index: int,
    action: EditAction,
    lineage: LineageRecord | None,
    compressor: _BehaviorCachedCompressor,
    parent_result: CompressionResult,
    minimum_controls: int,
) -> dict[str, Any]:
    flags = {name: False for name in ENDPOINT_NAMES}
    row: dict[str, Any] = {
        "raw_action_index": raw_action_index,
        "action": _action_dict(action),
        "action_hash": action.action_hash,
        "expected_old_subtree_hash": action.expected_old_subtree_hash,
        "endpoint_flags": flags,
    }
    if lineage is None:
        row["lineage_failure"] = "not_in_frozen_reachable_lineage_set"
        return row
    flags["K1"] = True
    child_direct = spark_closure._direct_hit(compressor, lineage.child_ast)
    child_result = compressor.run(
        lineage.child_ast, max_rounds=spark_closure.CLOSURE_MAX_ROUNDS
    )
    k2 = _is_k2(child_result, child_direct_hit=child_direct)
    k3 = bool(k2 and not parent_result.exact_identification)
    flags["K2"] = k2
    flags["K3"] = k3
    bundle_sha, bundle_payload = _full_pool_bundle(lineage)
    groups = _unique_replacement_groups(lineage)
    row.update(
        {
            "lineage_failure": None,
            "lineage_hash": lineage.lineage_hash,
            "child_canonical_hash": lineage.child_canonical_hash,
            "child_behavior_hash": lineage.child_behavior_hash,
            "child_direct_hit": child_direct,
            "child_trajectory": _trajectory(child_result),
            "full_pool_counterfactual_bundle_sha256": bundle_sha,
            "full_pool_counterfactual_bundle": bundle_payload,
            "replacement_pool_syntactic_count": len(lineage.matched_replacements),
            "replacement_pool_unique_behavior_count": len(groups),
            "control_outcomes_evaluated": k3,
        }
    )
    if not k3:
        row["full_pool_gate"] = {
            "K4_full_pool": False,
            "requires_K3_passed": False,
            "minimum_unique_control_behaviors": minimum_controls,
            "unique_control_behavior_count": len(groups),
            "controls_not_run_because_K3_failed": True,
        }
        return row

    # Run one deterministic representative per complete-domain behaviour.
    # Equivalent syntaxes inherit that behaviour's result and are supplied to
    # the pure predicate as an explicit conflict audit.
    outcomes: list[dict[str, Any]] = []
    detailed: list[dict[str, Any]] = []
    for behavior, equivalents in groups:
        representative = equivalents[0]
        result = compressor.run(
            representative.child_ast,
            max_rounds=spark_closure.CLOSURE_MAX_ROUNDS,
        )
        for _replacement in equivalents:
            outcomes.append(
                {
                    "child_behavior_hash": behavior,
                    "exact_identification": result.exact_identification,
                    "N_T": result.N_T,
                }
            )
        detailed.append(
            {
                "child_behavior_hash": behavior,
                "representative_child_canonical_hash": (
                    representative.child_canonical_hash
                ),
                "representative_motif_id": representative.motif_id,
                "equivalent_syntax_count": len(equivalents),
                "equivalent_motif_ids": sorted(item.motif_id for item in equivalents),
                "exact_identification": result.exact_identification,
                "N_T": result.N_T,
            }
        )
    gate = classify_full_pool_controls(
        k3=True,
        control_outcomes=outcomes,
        minimum_unique_control_behaviors=minimum_controls,
    )
    if gate["unique_control_behavior_count"] != len(groups):
        raise StrongK4ScanError("control behavior deduplication drifted")
    flags["K4_full_pool"] = bool(gate["K4_full_pool"])
    row["full_pool_gate"] = {
        **{key: value for key, value in gate.items() if key != "unique_control_outcomes"},
        "controls_not_run_because_K3_failed": False,
        "unique_control_outcomes": detailed,
    }
    if not (
        (not flags["K2"] or flags["K1"])
        and (not flags["K3"] or flags["K2"])
        and (not flags["K4_full_pool"] or flags["K3"])
    ):
        raise StrongK4ScanError("strong-K4 endpoints are not nested")
    return row


def _representation_counts(actions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    control_ready = [row for row in actions if row["endpoint_flags"]["K1"]]
    return {
        "universe_counts": {
            "raw_syntactic_actions": len(actions),
            "control_ready_actions": len(control_ready),
            "unique_child_behaviors": len(
                {row["child_behavior_hash"] for row in control_ready}
            ),
            "unique_full_pool_counterfactual_bundles": len(
                {
                    row["full_pool_counterfactual_bundle_sha256"]
                    for row in control_ready
                }
            ),
        },
        "endpoint_counts": {
            "raw_syntactic_actions": {
                endpoint: sum(bool(row["endpoint_flags"][endpoint]) for row in actions)
                for endpoint in ENDPOINT_NAMES
            },
            "unique_child_behaviors": {
                endpoint: len(
                    {
                        row["child_behavior_hash"]
                        for row in control_ready
                        if row["endpoint_flags"][endpoint]
                    }
                )
                for endpoint in ENDPOINT_NAMES
            },
            "unique_full_pool_counterfactual_bundles": {
                endpoint: len(
                    {
                        row["full_pool_counterfactual_bundle_sha256"]
                        for row in control_ready
                        if row["endpoint_flags"][endpoint]
                    }
                )
                for endpoint in ENDPOINT_NAMES
            },
        },
        "endpoint_opportunity": {
            endpoint: any(row["endpoint_flags"][endpoint] for row in actions)
            for endpoint in ENDPOINT_NAMES
        },
    }


_PUBLIC_FORBIDDEN_KEYS = {
    "target_seed",
    "target_index",
    "world_hash",
    "target_canonical_hash",
    "target_seed_namespace_sha256",
    "trajectory",
    "N_T",
    "endpoint_flags",
    "eligible_action_identity",
    "control_outcomes",
}


def _assert_target_blind_public_projection(value: object) -> None:
    if isinstance(value, Mapping):
        forbidden = _PUBLIC_FORBIDDEN_KEYS.intersection(value)
        if forbidden:
            raise StrongK4ScanError(
                f"public projection contains private fields: {sorted(forbidden)}"
            )
        for child in value.values():
            _assert_target_blind_public_projection(child)
    elif isinstance(value, list):
        for child in value:
            _assert_target_blind_public_projection(child)


def build_public_candidate_projection(
    plan_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Materialize only target-independent D0/parent data for a frozen candidate."""

    candidate_index = int(plan_candidate["candidate_index"])
    world_seed = int(plan_candidate["world_seed"])
    public_world = spark_closure._target_free_public_world_entry(
        candidate_index, world_seed
    )
    unsigned = {
        "candidate_index": candidate_index,
        "world_seed": world_seed,
        "world": public_world,
        "slots": plan_candidate["slots"],
    }
    _assert_target_blind_public_projection(unsigned)
    return {**unsigned, "public_identity_sha256": _sha256_json(unsigned)}


def derive_private_target_seed(config: Mapping[str, Any], world_seed: int) -> int:
    """Apply the frozen full-digest target-seed derivation exactly once."""

    namespaces = config.get("private_target_and_public_motif_namespaces")
    if not isinstance(namespaces, Mapping) or not isinstance(
        namespaces.get("target_seed_namespace"), str
    ):
        raise StrongK4ScanError("private target namespace is malformed")
    return spark_closure._target_seed_for_namespace(
        world_seed, str(namespaces["target_seed_namespace"])
    )


def materialize_private_candidate_world(
    config: Mapping[str, Any], world_seed: int
) -> SparkWorld:
    """The scanner's single, auditable hidden-target materialization seam."""

    target_seed = derive_private_target_seed(config, world_seed)
    return generate_spark_world(world_seed, target_seed=target_seed)


def scan_candidate_world(
    config: Mapping[str, Any], plan_candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Exhaust one frozen 3 x 10 candidate world, with no model interaction."""

    candidate_index = int(plan_candidate["candidate_index"])
    world_seed = int(plan_candidate["world_seed"])
    target_namespace = config["private_target_and_public_motif_namespaces"][
        "target_seed_namespace"
    ]
    world = materialize_private_candidate_world(config, world_seed)
    parent = select_parent(world)
    compressor = _BehaviorCachedCompressor(SparkCompressor(world))
    parent_result = compressor.run(
        parent, max_rounds=spark_closure.CLOSURE_MAX_ROUNDS
    )
    lineages = spark_lineage.enumerate_reachable_children(world)
    indexed = _lineage_index(lineages)
    minimum_controls = int(config["endpoint"]["minimum_unique_control_behaviors"])
    slot_rows: list[dict[str, Any]] = []
    for slot in plan_candidate["slots"]:
        actions = _raw_actions(world, str(slot["motif_id"]))
        action_rows = [
            _analyze_action(
                raw_action_index=raw_index,
                action=action,
                lineage=indexed.get(action),
                compressor=compressor,
                parent_result=parent_result,
                minimum_controls=minimum_controls,
            )
            for raw_index, action in enumerate(actions)
        ]
        slot_rows.append(
            {
                "slot_id": slot["slot_id"],
                "slot_index": slot["slot_index"],
                "motif_id": slot["motif_id"],
                "motif_stratum": slot["motif_stratum"],
                "counts": _representation_counts(action_rows),
                "actions": action_rows,
            }
        )
    public = build_public_candidate_projection(plan_candidate)
    private_unsigned: dict[str, Any] = {
        "target_seed_namespace_sha256": spark_closure._target_seed_digest(
            world_seed, namespace=target_namespace
        ),
        "target_index": world.target_index,
        "world_hash": world.world_hash,
        "target_canonical_hash": dsl.canonical_hash(world.target),
        "parent_canonical_hash": dsl.canonical_hash(parent),
        "parent_trajectory": _trajectory(parent_result),
        "parent_closes_endpoint": parent_result.exact_identification,
        "slots": slot_rows,
        "counts": _representation_counts(
            [action for slot in slot_rows for action in slot["actions"]]
        ),
    }
    private = {
        **private_unsigned,
        "private_outcome_sha256": _sha256_json(private_unsigned),
    }
    unsigned = {
        "candidate_index": candidate_index,
        "world_seed": world_seed,
        "public_identity": public,
        "private_outcome": private,
        "compressor_cache": {
            "key": "world-local complete-domain behavior hash and max rounds",
            "hits": compressor.cache_hits,
            "misses": compressor.cache_misses,
        },
    }
    return {**unsigned, "candidate_world_sha256": _sha256_json(unsigned)}


def _flatten_actions(worlds: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        action
        for world in worlds
        for slot in world["private_outcome"]["slots"]
        for action in slot["actions"]
    ]


def _aggregate_worlds(worlds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    slots = [
        slot
        for world in worlds
        for slot in world["private_outcome"]["slots"]
    ]
    actions = _flatten_actions(worlds)
    raw_counts = {
        endpoint: sum(bool(row["endpoint_flags"][endpoint]) for row in actions)
        for endpoint in ENDPOINT_NAMES
    }
    semantic_counts = {
        endpoint: sum(
            slot["counts"]["endpoint_counts"]["unique_child_behaviors"][endpoint]
            for slot in slots
        )
        for endpoint in ENDPOINT_NAMES
    }
    bundle_counts = {
        endpoint: sum(
            slot["counts"]["endpoint_counts"]
            ["unique_full_pool_counterfactual_bundles"][endpoint]
            for slot in slots
        )
        for endpoint in ENDPOINT_NAMES
    }
    slot_opportunities = {
        endpoint: sum(bool(slot["counts"]["endpoint_opportunity"][endpoint]) for slot in slots)
        for endpoint in ENDPOINT_NAMES
    }
    world_opportunities = {
        endpoint: sum(
            any(
                slot["counts"]["endpoint_opportunity"][endpoint]
                for slot in world["private_outcome"]["slots"]
            )
            for world in worlds
        )
        for endpoint in ENDPOINT_NAMES
    }
    histogram: dict[str, int] = {}
    for action in actions:
        gate = action.get("full_pool_gate")
        if not isinstance(gate, Mapping):
            continue
        controls = gate.get("unique_control_outcomes", [])
        if not isinstance(controls, list):
            continue
        for control in controls:
            key = str(control["N_T"])
            histogram[key] = histogram.get(key, 0) + 1
    strata: dict[str, Any] = {}
    for stratum in spark_lineage.MOTIF_STRATA:
        stratum_slots = [slot for slot in slots if slot["motif_stratum"] == stratum]
        stratum_actions = [action for slot in stratum_slots for action in slot["actions"]]
        strata[stratum] = {
            "slot_count": len(stratum_slots),
            "raw_action_count": len(stratum_actions),
            "raw_action_endpoint_counts": {
                endpoint: sum(
                    bool(action["endpoint_flags"][endpoint])
                    for action in stratum_actions
                )
                for endpoint in ENDPOINT_NAMES
            },
            "slot_opportunity_counts": {
                endpoint: sum(
                    bool(slot["counts"]["endpoint_opportunity"][endpoint])
                    for slot in stratum_slots
                )
                for endpoint in ENDPOINT_NAMES
            },
            "eligible_world_count_K4_full_pool": len(
                {
                    int(world["candidate_index"])
                    for world in worlds
                    if any(
                        slot["motif_stratum"] == stratum
                        and slot["counts"]["endpoint_opportunity"]["K4_full_pool"]
                        for slot in world["private_outcome"]["slots"]
                    )
                }
            ),
        }
    return {
        "world_count": len(worlds),
        "slot_count": len(slots),
        "raw_syntactic_action_count": len(actions),
        "control_ready_action_count": raw_counts["K1"],
        "raw_action_endpoint_counts": raw_counts,
        "slot_local_unique_child_behavior_endpoint_counts": semantic_counts,
        "slot_local_unique_full_pool_bundle_endpoint_counts": bundle_counts,
        "slot_opportunity_counts": slot_opportunities,
        "world_opportunity_counts": world_opportunities,
        "evaluated_action_local_unique_control_N_T_histogram": dict(
            sorted(histogram.items(), key=lambda pair: int(pair[0]))
        ),
        "by_motif_stratum": strata,
    }


def build_scan_shard(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    config_file_sha256: str,
    start_index: int,
    count: int,
    require_current_source: bool = True,
) -> dict[str, Any]:
    """Compute one exact frozen stage and bind it to plan/config/source."""

    candidates = validate_scan_plan(
        config,
        plan,
        config_file_sha256=config_file_sha256,
        require_current_source=require_current_source,
    )
    stage = int(config["candidate_stream"]["stage_world_count"])
    if (
        type(start_index) is not int
        or type(count) is not int
        or start_index < 0
        or count != stage
        or start_index % stage != 0
    ):
        raise StrongK4ScanError("scan shard must be one aligned frozen 64-world stage")
    end = start_index + count
    if end > len(candidates):
        raise StrongK4ScanError("scan shard exceeds the frozen candidate stream")
    manifest = str(plan["source_manifest_sha256"])
    worlds = [
        scan_candidate_world(config, candidates[index])
        for index in range(start_index, end)
    ]
    if require_current_source:
        _assert_current_source_manifest(manifest)
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": SHARD_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": config["evidence_scope"],
        "config_file_sha256": config_file_sha256,
        "plan_sha256": plan["plan_sha256"],
        "source_manifest_sha256": manifest,
        "candidate_range": {
            "start": start_index,
            "count": count,
            "end_exclusive": end,
        },
        "aggregate": _aggregate_worlds(worlds),
        "worlds": worlds,
        "post_hoc_explanatory_only": False,
        "outcome_conditioned_benchmark_construction": True,
        "historical_formal_K4_mutated": False,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "integrity_scope": INTEGRITY_SCOPE,
    }
    return {**unsigned, "shard_sha256": _sha256_json(unsigned)}


def _validate_shard(
    shard: Mapping[str, Any],
    *,
    config_file_sha256: str,
    plan: Mapping[str, Any],
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
        or shard.get("model_outputs_read") is not False
        or shard.get("provider_calls_made") != 0
        or shard.get("integrity_scope") != INTEGRITY_SCOPE
        or shard.get("outcome_conditioned_benchmark_construction") is not True
        or not isinstance(candidate_range, Mapping)
        or not isinstance(worlds, list)
    ):
        raise StrongK4ScanError("scan shard is malformed or tampered")
    start = candidate_range.get("start")
    count = candidate_range.get("count")
    end = candidate_range.get("end_exclusive")
    if (
        type(start) is not int
        or type(count) is not int
        or type(end) is not int
        or end != start + count
        or len(worlds) != count
        or [world.get("candidate_index") for world in worlds]
        != list(range(start, end))
    ):
        raise StrongK4ScanError("scan shard range or world order is malformed")
    plan_candidates = plan.get("candidates")
    if not isinstance(plan_candidates, list) or len(plan_candidates) != 1024:
        raise StrongK4ScanError("scan plan candidates are malformed")
    for world in worlds:
        if not isinstance(world, Mapping):
            raise StrongK4ScanError("scan shard world is malformed")
        world_unsigned = {
            key: value for key, value in world.items() if key != "candidate_world_sha256"
        }
        if world.get("candidate_world_sha256") != _sha256_json(world_unsigned):
            raise StrongK4ScanError("candidate world digest mismatch")
        candidate_index_value = world.get("candidate_index")
        if type(candidate_index_value) is not int:
            raise StrongK4ScanError("candidate world index is malformed")
        candidate_index = candidate_index_value
        plan_candidate = plan_candidates[candidate_index]
        public = world.get("public_identity")
        private = world.get("private_outcome")
        if not isinstance(plan_candidate, Mapping) or not isinstance(
            public, Mapping
        ) or not isinstance(private, Mapping):
            raise StrongK4ScanError("candidate public/private partition is malformed")
        public_unsigned = {
            key: value for key, value in public.items() if key != "public_identity_sha256"
        }
        private_unsigned = {
            key: value for key, value in private.items() if key != "private_outcome_sha256"
        }
        _assert_target_blind_public_projection(public_unsigned)
        public_world = public.get("world")
        if (
            world.get("world_seed") != plan_candidate.get("world_seed")
            or public.get("candidate_index") != candidate_index
            or public.get("world_seed") != plan_candidate.get("world_seed")
            or public.get("slots") != plan_candidate.get("slots")
            or not isinstance(public_world, Mapping)
            or public_world.get("world_index") != candidate_index
            or public_world.get("world_seed") != plan_candidate.get("world_seed")
            or public.get("public_identity_sha256") != _sha256_json(public_unsigned)
            or private.get("private_outcome_sha256") != _sha256_json(private_unsigned)
        ):
            raise StrongK4ScanError("candidate public/private identity binding drifted")
        private_slots = private.get("slots")
        plan_slots = plan_candidate.get("slots")
        if (
            not isinstance(private_slots, list)
            or not isinstance(plan_slots, list)
            or len(private_slots) != 3
            or len(plan_slots) != 3
        ):
            raise StrongK4ScanError("candidate private slots are malformed")
        for private_slot, plan_slot in zip(
            private_slots, plan_slots, strict=True
        ):
            if not isinstance(private_slot, Mapping) or not isinstance(
                plan_slot, Mapping
            ):
                raise StrongK4ScanError("candidate slot is malformed")
            if any(
                private_slot.get(key) != plan_slot.get(key)
                for key in ("slot_id", "slot_index", "motif_id", "motif_stratum")
            ):
                raise StrongK4ScanError("candidate private slot assignment drifted")
            actions = private_slot.get("actions")
            if (
                not isinstance(actions, list)
                or len(actions) != 10
                or any(not isinstance(action, Mapping) for action in actions)
                or [action.get("raw_action_index") for action in actions]
                != list(range(10))
            ):
                raise StrongK4ScanError("candidate slot action census is malformed")
            for action in actions:
                if not isinstance(action, Mapping):
                    raise StrongK4ScanError("candidate action is malformed")
                flags = action.get("endpoint_flags")
                if (
                    not isinstance(flags, Mapping)
                    or set(flags) != set(ENDPOINT_NAMES)
                    or any(type(flags[name]) is not bool for name in ENDPOINT_NAMES)
                    or (flags["K2"] and not flags["K1"])
                    or (flags["K3"] and not flags["K2"])
                    or (flags["K4_full_pool"] and not flags["K3"])
                ):
                    raise StrongK4ScanError("candidate action endpoints are malformed")
                if flags["K1"]:
                    bundle = action.get("full_pool_counterfactual_bundle")
                    if (
                        not isinstance(bundle, Mapping)
                        or action.get("full_pool_counterfactual_bundle_sha256")
                        != _sha256_json(bundle)
                    ):
                        raise StrongK4ScanError("full-pool bundle digest mismatch")
            if private_slot.get("counts") != _representation_counts(actions):
                raise StrongK4ScanError("candidate slot representation counts drifted")
        private_actions = [
            action for slot in private_slots for action in slot["actions"]
        ]
        if private.get("counts") != _representation_counts(private_actions):
            raise StrongK4ScanError("candidate world representation counts drifted")
    if shard.get("aggregate") != _aggregate_worlds(worlds):
        raise StrongK4ScanError("scan shard aggregate drifted")
    return start, end, worlds


def _matching_for_capacity(
    eligible_by_world: Mapping[int, Sequence[str]],
    *,
    strata: Sequence[str],
    capacity_per_stratum: int,
) -> dict[str, Any]:
    """Return the lexicographically first exact joint b-matching, if one exists.

    The comparison vector concatenates, in frozen stratum order, each
    stratum's ascending candidate indices.  A feasibility oracle is queried at
    each vector position, so marginal per-stratum counts can never substitute
    for a joint solution with world capacity one.
    """

    frozen_strata = tuple(strata)
    if len(set(frozen_strata)) != len(frozen_strata):
        raise StrongK4ScanError("matching strata are duplicated")
    order = {stratum: index for index, stratum in enumerate(frozen_strata)}
    normalized: dict[int, tuple[str, ...]] = {}
    for candidate_index, eligible in eligible_by_world.items():
        if type(candidate_index) is not int or candidate_index < 0:
            raise StrongK4ScanError("matching candidate index is malformed")
        if any(value not in order for value in eligible):
            raise StrongK4ScanError("matching contains an unknown stratum")
        values = tuple(sorted(set(eligible), key=order.__getitem__))
        normalized[candidate_index] = values
    if type(capacity_per_stratum) is not int or capacity_per_stratum < 1:
        raise StrongK4ScanError("matching capacity must be positive")
    required = capacity_per_stratum * len(frozen_strata)

    def completion_feasible(
        fixed: Mapping[int, str],
        lower_bound_for_active: Mapping[str, int],
    ) -> bool:
        fixed_counts = {
            stratum: sum(value == stratum for value in fixed.values())
            for stratum in frozen_strata
        }
        if (
            len(fixed) != len(set(fixed))
            or any(count > capacity_per_stratum for count in fixed_counts.values())
        ):
            return False
        right_nodes = tuple(
            (stratum, position)
            for stratum in frozen_strata
            for position in range(capacity_per_stratum - fixed_counts[stratum])
        )
        if not right_nodes:
            return True
        owner: dict[tuple[str, int], int] = {}

        def augment(candidate: int, seen: set[tuple[str, int]]) -> bool:
            for stratum in normalized[candidate]:
                if candidate <= lower_bound_for_active.get(stratum, -1):
                    continue
                for node in right_nodes:
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
            if len(owner) == len(right_nodes):
                return True
        return False

    if not completion_feasible({}, {}):
        # Retain a deterministic maximum-cardinality diagnostic without ever
        # representing it as a feasible balanced cohort.
        right_nodes = tuple(
            (stratum, position)
            for stratum in frozen_strata
            for position in range(capacity_per_stratum)
        )
        owner: dict[tuple[str, int], int] = {}

        def augment_max(candidate: int, seen: set[tuple[str, int]]) -> bool:
            for stratum in normalized[candidate]:
                for node in right_nodes:
                    if node[0] != stratum or node in seen:
                        continue
                    seen.add(node)
                    incumbent = owner.get(node)
                    if incumbent is None or augment_max(incumbent, seen):
                        owner[node] = candidate
                        return True
            return False

        for candidate in sorted(normalized):
            augment_max(candidate, set())
        partial = sorted(
            (
                {"candidate_index": candidate, "construction_stratum": node[0]}
                for node, candidate in owner.items()
            ),
            key=lambda row: int(row["candidate_index"]),
        )
        return {
            "capacity_per_stratum": capacity_per_stratum,
            "required_world_count": required,
            "matched_world_count": len(partial),
            "complete": False,
            "counts_by_construction_stratum": {
                stratum: sum(
                    row["construction_stratum"] == stratum for row in partial
                )
                for stratum in frozen_strata
            },
            "assignments": partial,
            "lexicographic_assignment_vector": None,
        }

    # Greedily fix the next smallest vector element only when a joint exact
    # completion remains possible.  This constructs the unique frozen tie-break.
    fixed: dict[int, str] = {}
    vector: list[int] = []
    for stratum in frozen_strata:
        lower = -1
        for _position in range(capacity_per_stratum):
            chosen: int | None = None
            for candidate in sorted(normalized):
                if (
                    candidate <= lower
                    or candidate in fixed
                    or stratum not in normalized[candidate]
                ):
                    continue
                trial = {**fixed, candidate: stratum}
                if completion_feasible(trial, {stratum: candidate}):
                    chosen = candidate
                    break
            if chosen is None:
                raise StrongK4ScanError(
                    "lexicographic matching lost a previously feasible completion"
                )
            fixed[chosen] = stratum
            vector.append(chosen)
            lower = chosen
    assignment_by_world = fixed
    assignments = [
        {
            "candidate_index": candidate,
            "construction_stratum": assignment_by_world[candidate],
            "eligible_focal_strata": list(normalized[candidate]),
        }
        for candidate in sorted(assignment_by_world)
    ]
    counts = {
        stratum: sum(
            row["construction_stratum"] == stratum for row in assignments
        )
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
        "tie_break_definition": (
            "concatenate ascending candidate indices for each MOTIF_STRATA entry; "
            "choose the lexicographically minimum jointly feasible vector"
        ),
    }


def deterministic_balanced_matching(
    eligible_by_world: Mapping[int, Sequence[str]],
    *,
    strata: Sequence[str] = spark_lineage.MOTIF_STRATA,
    target_per_stratum: int = 8,
    fallback_per_stratum: int = 6,
) -> dict[str, Any]:
    """Select q=8, else q=6, without ranking by action count or outcomes."""

    frozen_strata = tuple(strata)
    marginal_capacity = {
        stratum: len(
            {
                candidate
                for candidate, eligible in eligible_by_world.items()
                if stratum in eligible
            }
        )
        for stratum in frozen_strata
    }
    marginal_audit = {
        stratum: {
            "eligible_world_capacity": marginal_capacity[stratum],
            "target_q": target_per_stratum,
            "target_q_deficit": max(
                0, target_per_stratum - marginal_capacity[stratum]
            ),
            "fallback_q": fallback_per_stratum,
            "fallback_q_deficit": max(
                0, fallback_per_stratum - marginal_capacity[stratum]
            ),
        }
        for stratum in frozen_strata
    }
    full = _matching_for_capacity(
        eligible_by_world,
        strata=frozen_strata,
        capacity_per_stratum=target_per_stratum,
    )
    maximum_joint_q = target_per_stratum
    if not full["complete"]:
        maximum_joint_q = 0
        for q in range(target_per_stratum - 1, 0, -1):
            if _matching_for_capacity(
                eligible_by_world,
                strata=frozen_strata,
                capacity_per_stratum=q,
            )["complete"]:
                maximum_joint_q = q
                break
    capacity_audit = {
        "marginal_by_stratum": marginal_audit,
        "maximum_joint_balanced_q_up_to_target": maximum_joint_q,
        "target_q": target_per_stratum,
        "joint_target_q_deficit": target_per_stratum - maximum_joint_q,
        "joint_target_world_deficit": (
            target_per_stratum - maximum_joint_q
        )
        * len(frozen_strata),
        "marginal_counts_alone_used_for_classification": False,
    }
    if full["complete"]:
        return {
            "classification": "full_32_balanced_feasible",
            **full,
            "capacity_audit": capacity_audit,
        }
    fallback = _matching_for_capacity(
        eligible_by_world,
        strata=frozen_strata,
        capacity_per_stratum=fallback_per_stratum,
    )
    if fallback["complete"]:
        return {
            "classification": "reduced_24_balanced_feasible",
            **fallback,
            "capacity_audit": capacity_audit,
        }
    return {
        "classification": "balanced_strong_K4_benchmark_not_feasible_under_cap",
        **fallback,
        "capacity_audit": capacity_audit,
        "failed_full_capacity_audit": {
            key: value for key, value in full.items() if key != "assignments"
        },
    }


def _diversity_summary(identities: Sequence[str]) -> dict[str, Any]:
    counts = Counter(identities)
    repeated = {identity: count for identity, count in counts.items() if count > 1}
    return {
        "observation_count": len(identities),
        "unique_identity_count": len(counts),
        "repeated_unique_identity_count": len(repeated),
        "repeated_observation_excess_count": sum(
            count - 1 for count in repeated.values()
        ),
        "maximum_identity_multiplicity": max(counts.values(), default=0),
        "repeated_identity_multiplicities": [
            {"identity_sha256": identity, "count": repeated[identity]}
            for identity in sorted(repeated)
        ],
    }


def _strong_k4_actions_for_stratum(
    world: Mapping[str, Any], stratum: str
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    return [
        (slot, action)
        for slot in world["private_outcome"]["slots"]
        if slot["motif_stratum"] == stratum
        for action in slot["actions"]
        if action["endpoint_flags"]["K4_full_pool"]
    ]


def _cohort_diversity_report(
    worlds: Sequence[Mapping[str, Any]],
    assignments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_index = {int(world["candidate_index"]): world for world in worlds}
    targets: list[str] = []
    children: list[str] = []
    motifs: list[str] = []
    frames: list[str] = []
    packages: list[str] = []
    selected_children_by_stratum = {
        stratum: [] for stratum in spark_lineage.MOTIF_STRATA
    }
    for assignment in assignments:
        candidate_index = int(assignment["candidate_index"])
        stratum = str(assignment["construction_stratum"])
        world = by_index[candidate_index]
        target_hash = str(world["private_outcome"]["target_canonical_hash"])
        targets.append(target_hash)
        witnesses = _strong_k4_actions_for_stratum(world, stratum)
        if not witnesses:
            raise StrongK4ScanError("cohort diversity lost its strong-K4 witness")
        for slot, action in witnesses:
            child_hash = str(action["child_behavior_hash"])
            motif_hash = hashlib.sha256(
                str(slot["motif_id"]).encode("utf-8")
            ).hexdigest()
            frame = action["full_pool_counterfactual_bundle"]["frame"]
            frame_hash = _sha256_json(frame)
            children.append(child_hash)
            selected_children_by_stratum[stratum].append(child_hash)
            motifs.append(motif_hash)
            frames.append(frame_hash)
            packages.append(
                _sha256_json(
                    {
                        "target_canonical_hash": target_hash,
                        "child_behavior_hash": child_hash,
                        "motif_id_sha256": motif_hash,
                        "frame_sha256": frame_hash,
                    }
                )
            )

    global_children_by_stratum: dict[str, set[str]] = {
        stratum: set() for stratum in spark_lineage.MOTIF_STRATA
    }
    for world in worlds:
        for stratum in spark_lineage.MOTIF_STRATA:
            global_children_by_stratum[stratum].update(
                str(action["child_behavior_hash"])
                for _slot, action in _strong_k4_actions_for_stratum(world, stratum)
            )
    global_children = set().union(*global_children_by_stratum.values())
    low_diversity = len(global_children) < 4 or any(
        len(global_children_by_stratum[stratum]) <= 1
        for stratum in spark_lineage.MOTIF_STRATA
    )
    return {
        "selected_world_count": len(assignments),
        "selected_witness_action_count": len(children),
        "selected_target_canonical_hash_diversity": _diversity_summary(targets),
        "selected_child_behavior_diversity": _diversity_summary(children),
        "selected_motif_id_diversity": _diversity_summary(motifs),
        "selected_action_frame_diversity": _diversity_summary(frames),
        "selected_target_child_motif_frame_package_diversity": (
            _diversity_summary(packages)
        ),
        "selected_unique_child_behaviors_by_construction_stratum": {
            stratum: len(set(selected_children_by_stratum[stratum]))
            for stratum in spark_lineage.MOTIF_STRATA
        },
        "global_qualifying_unique_child_behavior_count": len(global_children),
        "global_qualifying_unique_child_behaviors_by_motif_stratum": {
            stratum: len(global_children_by_stratum[stratum])
            for stratum in spark_lineage.MOTIF_STRATA
        },
        "low_semantic_diversity": low_diversity,
        "low_semantic_diversity_rule": (
            "global qualifying unique child behaviors < 4 or any motif stratum "
            "has <= 1 qualifying unique child behavior"
        ),
        "selection_changed_for_diversity": False,
    }


def merge_scan_shards(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    shards: Sequence[Mapping[str, Any]],
    *,
    config_file_sha256: str,
    require_current_source: bool = True,
) -> dict[str, Any]:
    """Validate and merge the exact 0..1023 scan before balanced selection."""

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
        )
        for shard in shards
    ]
    validated.sort(key=lambda item: item[0])
    cursor = 0
    worlds: list[Mapping[str, Any]] = []
    ranges: list[dict[str, int]] = []
    stage = int(config["candidate_stream"]["stage_world_count"])
    for start, end, rows in validated:
        if start != cursor:
            reason = "overlap" if start < cursor else "gap"
            raise StrongK4ScanError(f"scan shard ranges contain a {reason}")
        if end - start != stage:
            raise StrongK4ScanError("scan shard is not one frozen stage")
        worlds.extend(rows)
        ranges.append({"start": start, "end_exclusive": end})
        cursor = end
    if cursor != len(candidates) or len(worlds) != len(candidates):
        raise StrongK4ScanError("merge requires the complete fixed 1024-world scan")
    if [world["candidate_index"] for world in worlds] != list(range(1024)):
        raise StrongK4ScanError("merged candidates are not in original index order")
    eligible_by_world: dict[int, tuple[str, ...]] = {}
    for world in worlds:
        eligible_by_world[int(world["candidate_index"])] = tuple(
            stratum
            for stratum in spark_lineage.MOTIF_STRATA
            if any(
                slot["motif_stratum"] == stratum
                and slot["counts"]["endpoint_opportunity"]["K4_full_pool"]
                for slot in world["private_outcome"]["slots"]
            )
        )
    matching = deterministic_balanced_matching(eligible_by_world)
    by_index = {int(world["candidate_index"]): world for world in worlds}
    enriched_assignments: list[dict[str, Any]] = []
    for assignment in matching["assignments"]:
        world = by_index[int(assignment["candidate_index"])]
        stratum = str(assignment["construction_stratum"])
        witnesses = [
            str(slot["slot_id"])
            for slot in world["private_outcome"]["slots"]
            if slot["motif_stratum"] == stratum
            and slot["counts"]["endpoint_opportunity"]["K4_full_pool"]
        ]
        if matching["complete"] and not witnesses:
            raise StrongK4ScanError("selected construction stratum has no witness slot")
        enriched_assignments.append(
            {
                **assignment,
                "K4_full_pool_witness_slot_ids": witnesses,
                "all_three_slots_claimed_eligible": False,
            }
        )
    matching = {**matching, "assignments": enriched_assignments}
    diversity = _cohort_diversity_report(worlds, enriched_assignments)
    public_projection = [
        by_index[row["candidate_index"]]["public_identity"]
        for row in matching["assignments"]
    ]
    _assert_target_blind_public_projection(public_projection)
    matching = {
        **matching,
        "selection_unit": "unique candidate world",
        "ranking_fields_used": [],
        "diversity_and_repetition_audit": diversity,
        "low_semantic_diversity": diversity["low_semantic_diversity"],
        "future_public_projection": public_projection,
        "future_public_projection_sha256": _sha256_json(public_projection),
        "future_public_projection_marks_witness_slot": False,
        "eligibility_claim_scope": (
            "at least one private witness slot in the assigned construction stratum; "
            "the three public slots are not all claimed eligible"
        ),
    }
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": MERGED_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": config["evidence_scope"],
        "config_file_sha256": config_file_sha256,
        "plan_sha256": plan["plan_sha256"],
        "source_manifest_sha256": plan["source_manifest_sha256"],
        "complete_candidate_range": {"start": 0, "count": 1024, "end_exclusive": 1024},
        "shard_ranges": ranges,
        "aggregate": _aggregate_worlds(worlds),
        "balanced_cohort": matching,
        "worlds": worlds,
        "post_hoc_explanatory_only": False,
        "outcome_conditioned_benchmark_construction": True,
        "historical_formal_K4_mutated": False,
        "interpretation_limit": config["artifact_contract"]["interpretation_limit"],
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "integrity_scope": INTEGRITY_SCOPE,
    }
    if require_current_source:
        _assert_current_source_manifest(str(plan["source_manifest_sha256"]))
    return {**unsigned, "scan_sha256": _sha256_json(unsigned)}


def _read_json_bytes(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise StrongK4ScanError(f"cannot read JSON input {path}") from exc
    if not isinstance(value, dict):
        raise StrongK4ScanError(f"JSON input must be an object: {path}")
    return value, _sha256_bytes(payload)


def _emit_json_exclusive_0600(value: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
    try:
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise StrongK4ScanError(f"refusing to overwrite artifact {output}") from exc
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
    parser = argparse.ArgumentParser(
        description="Offline strong full-pool K4 benchmark-feasibility scan"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--config", type=Path, required=True)
    plan_parser.add_argument("--registry", type=Path, required=True)
    plan_parser.add_argument("--output", type=Path, required=True)
    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--config", type=Path, required=True)
    scan_parser.add_argument("--plan", type=Path, required=True)
    scan_parser.add_argument("--shard-index", type=int)
    scan_parser.add_argument("--start-index", type=int)
    scan_parser.add_argument("--count", type=int)
    scan_parser.add_argument("--output", type=Path, required=True)
    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--config", type=Path, required=True)
    merge_parser.add_argument("--plan", type=Path, required=True)
    merge_parser.add_argument("--inputs", type=Path, nargs="+", required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    config, config_sha = _read_json_bytes(args.config)
    if args.command == "plan":
        registry, registry_sha = _read_json_bytes(args.registry)
        manifest = _current_source_manifest_sha256()
        result = build_target_free_scan_plan(
            config,
            registry,
            config_file_sha256=config_sha,
            registry_file_sha256=registry_sha,
            source_manifest_sha256=manifest,
        )
        _assert_current_source_manifest(manifest)
    else:
        plan, _plan_file_sha = _read_json_bytes(args.plan)
        if args.command == "scan":
            stage = int(config.get("candidate_stream", {}).get("stage_world_count", 0))
            if args.shard_index is not None:
                if args.start_index is not None or args.count is not None:
                    raise StrongK4ScanError(
                        "use either --shard-index or --start-index/--count"
                    )
                if type(args.shard_index) is not int or not 0 <= args.shard_index < 16:
                    raise StrongK4ScanError("shard index must be in 0..15")
                start_index = args.shard_index * stage
                count = stage
            else:
                if args.start_index is None or args.count is None:
                    raise StrongK4ScanError(
                        "scan requires --shard-index or both --start-index and --count"
                    )
                start_index = args.start_index
                count = args.count
            result = build_scan_shard(
                config,
                plan,
                config_file_sha256=config_sha,
                start_index=start_index,
                count=count,
            )
        else:
            shards = [_read_json_bytes(path)[0] for path in args.inputs]
            result = merge_scan_shards(
                config,
                plan,
                shards,
                config_file_sha256=config_sha,
            )
    _emit_json_exclusive_0600(result, args.output)
    return 0


__all__ = [
    "CONFIG_KIND",
    "ENDPOINT_NAMES",
    "INTEGRITY_SCOPE",
    "MERGED_KIND",
    "PLAN_KIND",
    "PROTOCOL_ID",
    "SCHEMA_VERSION",
    "SHARD_KIND",
    "StrongK4ScanError",
    "build_public_candidate_projection",
    "build_scan_shard",
    "build_target_free_scan_plan",
    "classify_full_pool_controls",
    "derive_candidate_seed_vector",
    "derive_candidate_world_seed",
    "derive_private_target_seed",
    "deterministic_balanced_matching",
    "main",
    "materialize_private_candidate_world",
    "merge_scan_shards",
    "scan_candidate_world",
    "validate_config",
    "validate_scan_plan",
    "validate_seed_registry",
]


if __name__ == "__main__":
    raise SystemExit(main())
