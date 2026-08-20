"""Offline post-hoc action-opportunity map for the sealed matched triad.

The formal matched-triad result remains unchanged.  This module asks a narrower
descriptive question after target analysis was already opened: for each of the
96 frozen world/motif slots, did any of the ten grammar actions have the
capacity to reach the already-frozen K2/K3/K4 endpoints?  It never calls a
model or a provider.

The historical plan intentionally binds an older source manifest.  We do not
weaken the live-generation or joint-analysis barriers to accommodate that
drift.  Instead, this separate offline diagnostic accepts exactly one
allowlisted plan/generation/analysis triple, validates all of its remaining
sealed structure, and binds its result to one stable *current* source manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import dsl, spark_closure, spark_cross_model, spark_lineage
from .provenance import PROJECT_ROOT, source_manifest
from .spark_compressor import CompressionResult, SparkCompressor
from .spark_lineage import EditAction, LineageRecord, motif_by_id, select_parent
from .spark_world import SparkWorld


OPPORTUNITY_MAP_SCHEMA_VERSION = 1
OPPORTUNITY_MAP_KIND = "spark-cross-model-posthoc-action-opportunity-map"
OPPORTUNITY_LANDSCAPE_KIND = (
    "spark-cross-model-posthoc-action-opportunity-landscape"
)
OPPORTUNITY_OVERLAY_KIND = "spark-cross-model-posthoc-model-action-overlay"
OPPORTUNITY_DIAGNOSTIC_ID = "post-hoc-explanatory-action-opportunity-map-v1"

# This is an offline exception for one immutable historical triple, not a new
# accepted live protocol.  Existing source-manifest barriers remain untouched.
SEALED_PLAN_SHA256 = (
    "a52c70b7cc8595ce1615dba1c5146576d23ff8330d0d44b2dd1de66ef9798064"
)
SEALED_GENERATION_BUNDLE_SHA256 = (
    "4f0ef3ff627f3e8c0df3667ff4668de57e3bbc2d16314b20f10bb9bd17c4e928"
)
SEALED_ANALYSIS_SHA256 = (
    "a4bbe0eb862d2af20690a759eb47ab7b1975f7197a20ecc34357251d556a6bb0"
)
SEALED_PLAN_SOURCE_MANIFEST_SHA256 = (
    "93c9baf7b915cce2f24e1103a10758cec54eeb8587fa6753df4643ab9f0ba027"
)
SEALED_PLAN_FILE_SHA256 = (
    "b20c99f517a4ecdad9400765d1e91c90a54a2f48248cdd2da925d44d65afbe5e"
)
SEALED_GENERATIONS_FILE_SHA256 = (
    "fac15a9f22e21984df6dc4c2e3f093b0f9ebfb0c45a8a94ee0853ea866efef42"
)
SEALED_ANALYSIS_FILE_SHA256 = (
    "1e0903abf8a8071d2ea29a54bc9964d3e263fd9481177489041f96be98f7ce44"
)
SEALED_GENERATION_SHA256_BY_ARM = {
    spark_cross_model.DEEPSEEK_FLASH_ARM_ID: (
        "0fbe1f1d3442e7450753f2f8c9167a1bef6cb638b7f70d60dc462becebbee0f3"
    ),
    spark_cross_model.DEEPSEEK_PRO_ARM_ID: (
        "a87d8b425b0145703bf79d06bd72de5ba21b1ac1f0d00e8f88993362714c7403"
    ),
    spark_cross_model.GLM_ARM_ID: (
        "d326f3cb80a7e0b1444cbb9d9ee4a9ca037c4726444f3f10988b439da253143f"
    ),
}

RAW_ACTIONS_PER_SLOT = 10
EXPECTED_SLOT_COUNT = 96
EXPECTED_WORLD_COUNT = 32
EXPECTED_RAW_ACTION_COUNT = RAW_ACTIONS_PER_SLOT * EXPECTED_SLOT_COUNT

_K_TO_LAYER = {"K1": "L", "K2": "M", "K3": "D", "K4": "R"}
_ENDPOINT_NAMES = tuple(_K_TO_LAYER)
_SEALED_COMMON_K3_WORLD_SEEDS = (
    3920682316420328816,
    6872575636001638699,
    3034756861122824323,
    4402357155133626695,
    4960748528416202938,
    3680507740242696405,
)
_SEALED_GLM_K4_WORLD_SEED = 7993937249025442561
_RECORD_IDENTITY_FIELDS = (
    "serial_index",
    "slot_id",
    "world_index",
    "world_seed",
    "slot_index",
    "condition",
    "motif_id",
    "motif_stratum",
    "world_identity_sha256",
    "slot_identity_sha256",
)


class OpportunityMapError(ValueError):
    """Raised when the sealed inputs or deterministic diagnostic are invalid."""


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


def _current_source_manifest_sha256() -> str:
    value = source_manifest(PROJECT_ROOT).get("source_manifest_sha256")
    if not isinstance(value, str) or len(value) != 64:
        raise OpportunityMapError("current source manifest is malformed")
    return value


def _assert_stable_source_manifest(before: str) -> str:
    after = _current_source_manifest_sha256()
    if after != before:
        raise OpportunityMapError(
            "diagnostic source manifest changed during offline computation"
        )
    return after


def _sealed_input_identity() -> dict[str, Any]:
    return {
        "plan_sha256": SEALED_PLAN_SHA256,
        "generation_bundle_sha256": SEALED_GENERATION_BUNDLE_SHA256,
        "analysis_sha256": SEALED_ANALYSIS_SHA256,
        "generation_sha256_by_arm": dict(SEALED_GENERATION_SHA256_BY_ARM),
        "historical_plan_source_manifest_sha256": (
            SEALED_PLAN_SOURCE_MANIFEST_SHA256
        ),
    }


def _validate_sealed_plan(
    plan: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if plan.get("plan_sha256") != SEALED_PLAN_SHA256:
        raise OpportunityMapError("plan is outside the exact sealed diagnostic allowlist")
    if plan.get("source_manifest_sha256") != SEALED_PLAN_SOURCE_MANIFEST_SHA256:
        raise OpportunityMapError("plan historical source manifest is not allowlisted")
    try:
        validated = spark_cross_model._validate_plan(plan)
    except (TypeError, ValueError) as exc:
        raise OpportunityMapError("sealed matched-triad plan failed validation") from exc
    return validated


def _validate_analysis_artifact(
    plan: Mapping[str, Any],
    bundle: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> None:
    unsigned = {key: value for key, value in analysis.items() if key != "analysis_sha256"}
    if analysis.get("analysis_sha256") != _sha256_json(unsigned):
        raise OpportunityMapError("sealed analysis digest mismatch")
    if analysis.get("analysis_sha256") != SEALED_ANALYSIS_SHA256:
        raise OpportunityMapError("analysis is outside the exact sealed allowlist")
    if (
        analysis.get("schema_version") != 1
        or analysis.get("kind")
        != "spark-cross-model-matched-triad-joint-analysis"
        or analysis.get("protocol_id") != spark_cross_model.CROSS_MODEL_PROTOCOL_ID
        or analysis.get("plan_sha256") != plan.get("plan_sha256")
        or analysis.get("public_identity_sha256")
        != plan.get("public_identity_sha256")
        or analysis.get("generation_bundle_sha256") != bundle.get("bundle_sha256")
        or analysis.get("generation_sha256_by_arm")
        != SEALED_GENERATION_SHA256_BY_ARM
        or analysis.get("all_three_96_record_arms_validated_before_analysis")
        is not True
        or analysis.get("balanced_288_call_execution_schedule_validated") is not True
    ):
        raise OpportunityMapError("sealed analysis bindings are malformed")
    joint = analysis.get("joint_analysis")
    if not isinstance(joint, Mapping):
        raise OpportunityMapError("sealed analysis joint result is malformed")
    arms = joint.get("arms")
    if not isinstance(arms, Mapping) or set(arms) != set(
        spark_cross_model.CROSS_MODEL_ARM_IDS
    ):
        raise OpportunityMapError("sealed analysis does not contain the exact triad")


def _validate_sealed_triple(
    plan: Mapping[str, Any],
    bundle: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    slots, routes, world_digests, slot_digests = _validate_sealed_plan(plan)
    if bundle.get("bundle_sha256") != SEALED_GENERATION_BUNDLE_SHA256:
        raise OpportunityMapError(
            "generation bundle is outside the exact sealed diagnostic allowlist"
        )
    try:
        generations = spark_cross_model._generations_from_bundle(plan, bundle)
    except (TypeError, ValueError) as exc:
        raise OpportunityMapError("sealed generation bundle failed validation") from exc

    by_arm: dict[str, Mapping[str, Any]] = {}
    identities: dict[str, tuple[tuple[Any, ...], ...]] = {}
    for route in routes:
        arm_id = str(route["arm_id"])
        matches = [row for row in generations if row.get("arm_id") == arm_id]
        if len(matches) != 1:
            raise OpportunityMapError("sealed generation triad arm set is malformed")
        generation = matches[0]
        if (
            generation.get("generation_sha256")
            != SEALED_GENERATION_SHA256_BY_ARM[arm_id]
        ):
            raise OpportunityMapError("route generation is outside the exact allowlist")
        try:
            records = spark_cross_model._validate_arm_generation(
                plan,
                generation,
                expected_route=route,
                slots=slots,
                world_digests=world_digests,
                slot_digests=slot_digests,
            )
        except (TypeError, ValueError) as exc:
            raise OpportunityMapError("sealed route generation failed validation") from exc
        identities[arm_id] = tuple(
            tuple(record[field] for field in _RECORD_IDENTITY_FIELDS)
            for record in records
        )
        by_arm[arm_id] = generation
    first = identities[spark_cross_model.CROSS_MODEL_ARM_IDS[0]]
    if any(
        identities[arm_id] != first
        for arm_id in spark_cross_model.CROSS_MODEL_ARM_IDS[1:]
    ):
        raise OpportunityMapError("sealed triad model records do not share identities")
    _validate_analysis_artifact(plan, bundle, analysis)
    return by_arm


def enumerate_raw_slot_actions(
    world: SparkWorld,
    motif_id: str,
) -> tuple[EditAction, ...]:
    """Return the ten target-independent grammar actions for one assigned motif."""

    parent = select_parent(world)
    motif = motif_by_id(motif_id)
    actions = tuple(spark_lineage._candidate_action_variants(parent, motif))
    if len(actions) != RAW_ACTIONS_PER_SLOT or len(set(actions)) != len(actions):
        raise OpportunityMapError("frozen raw action grammar is not exactly ten actions")
    return actions


def _parsed_action(action: EditAction) -> spark_closure.ParsedAction:
    return spark_closure.ParsedAction(
        operation=action.operation,
        path=action.path,  # type: ignore[arg-type]
        binary_operator=action.binary_operator,
        motif_side=action.motif_side,
    )


def _endpoint_flags(row: Mapping[str, Any]) -> dict[str, bool]:
    layered = spark_closure._layered_slot_endpoints(row)
    flags = {
        endpoint: bool(layered[layer])
        for endpoint, layer in _K_TO_LAYER.items()
    }
    if not (
        (not flags["K2"] or flags["K1"])
        and (not flags["K3"] or flags["K2"])
        and (not flags["K4"] or flags["K3"])
    ):
        raise OpportunityMapError("closure endpoints are not nested")
    return {
        **flags,
        "weak_at_least_one_replacement_failure": bool(
            layered["weak_at_least_one_replacement_failure"]
        ),
    }


class _BehaviorCachedCompressor:
    """Cache deterministic four-round results by complete-domain behavior."""

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
        # Endpoint and trajectory cardinalities depend only on the complete
        # seed behavior.  Keep the requested seed identity accurate when an
        # equivalent syntax reuses a cached computation.
        return replace(
            result,
            seed_ast=canonical,  # type: ignore[arg-type]
            seed_canonical_hash=canonical_hash,
        )


def _trajectory_projection(result: CompressionResult) -> dict[str, Any]:
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


def _lineage_by_action(
    lineages: Sequence[LineageRecord],
    action: EditAction,
) -> LineageRecord | None:
    matches = [lineage for lineage in lineages if lineage.action == action]
    if len(matches) > 1:
        raise OpportunityMapError("one raw action matched multiple frozen lineages")
    return matches[0] if matches else None


def _formal_bundle_id(lineage: LineageRecord) -> str:
    replacements = lineage.matched_replacements[:2]
    if len(replacements) != 2:
        raise OpportunityMapError("control-ready lineage lost its frozen replacements")
    return _sha256_json(
        {
            "child_behavior_sha256": lineage.child_behavior_hash,
            "ordered_formal_replacement_behavior_sha256": [
                replacement.child_behavior_hash for replacement in replacements
            ],
        }
    )


def _full_pool_bundle_id(lineage: LineageRecord) -> str:
    return _sha256_json(
        {
            "child_behavior_sha256": lineage.child_behavior_hash,
            "ordered_full_replacement_pool_behavior_sha256": [
                replacement.child_behavior_hash
                for replacement in lineage.matched_replacements
            ],
        }
    )


def _audit_replacement_pool(
    *,
    world: SparkWorld,
    parent: dsl.Expr,
    lineage: LineageRecord,
) -> None:
    """Prove every saved control replays in the focal same-frame bundle."""

    focal_motif = motif_by_id(lineage.motif_id)
    focal_frame = lineage.action.frame_key(focal_motif)
    for replacement in lineage.matched_replacements:
        motif = motif_by_id(replacement.motif_id)
        if (
            replacement.motif_stratum != motif.stratum
            or motif.stratum != lineage.motif_stratum
            or motif.complexity_bucket != lineage.motif_complexity_bucket
        ):
            raise OpportunityMapError(
                "matched replacement differs in stratum or complexity bucket"
            )
        replacement_action = EditAction(
            operation=lineage.action.operation,
            path=lineage.action.path,
            expected_old_subtree_hash=lineage.action.expected_old_subtree_hash,
            motif_id=motif.motif_id,
            binary_operator=lineage.action.binary_operator,
            motif_side=lineage.action.motif_side,
        )
        if replacement_action.frame_key(motif) != focal_frame:
            raise OpportunityMapError("matched replacement differs from focal frame")
        try:
            replayed = spark_lineage.validate_lineage(
                world,
                parent,
                motif,
                replacement_action,
                expected_child=replacement.child_ast,
            )
        except (TypeError, ValueError) as exc:
            raise OpportunityMapError("matched replacement does not replay") from exc
        if (
            replayed.child_canonical_hash != replacement.child_canonical_hash
            or replayed.child_behavior_hash != replacement.child_behavior_hash
        ):
            raise OpportunityMapError("matched replacement replay identity differs")


def _fraction(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _representation_counts(
    actions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    raw_counts = {
        endpoint: sum(
            bool(action["endpoint_flags"][endpoint]) for action in actions
        )
        for endpoint in _ENDPOINT_NAMES
    }
    valid = [action for action in actions if action["endpoint_flags"]["K1"]]
    behavior_universe = {
        str(action["child_behavior_hash"]) for action in valid
    }
    bundle_universe = {
        str(action["formal_counterfactual_bundle_sha256"]) for action in valid
    }
    behavior_counts = {
        endpoint: len(
            {
                str(action["child_behavior_hash"])
                for action in valid
                if action["endpoint_flags"][endpoint]
            }
        )
        for endpoint in _ENDPOINT_NAMES
    }
    bundle_counts = {
        endpoint: len(
            {
                str(action["formal_counterfactual_bundle_sha256"])
                for action in valid
                if action["endpoint_flags"][endpoint]
            }
        )
        for endpoint in _ENDPOINT_NAMES
    }
    return {
        "universe_counts": {
            "raw_syntactic_actions": len(actions),
            "control_ready_actions": len(valid),
            "unique_child_behaviors": len(behavior_universe),
            "unique_formal_counterfactual_bundles": len(bundle_universe),
        },
        "endpoint_counts": {
            "raw_syntactic_actions": raw_counts,
            "unique_child_behaviors": behavior_counts,
            "unique_formal_counterfactual_bundles": bundle_counts,
        },
        "endpoint_density": {
            "raw_syntactic_actions": {
                endpoint: _fraction(raw_counts[endpoint], len(actions))
                for endpoint in _ENDPOINT_NAMES
            },
            "unique_child_behaviors": {
                endpoint: _fraction(behavior_counts[endpoint], len(behavior_universe))
                for endpoint in _ENDPOINT_NAMES
            },
            "unique_formal_counterfactual_bundles": {
                endpoint: _fraction(bundle_counts[endpoint], len(bundle_universe))
                for endpoint in _ENDPOINT_NAMES
            },
        },
        "endpoint_opportunity": {
            endpoint: raw_counts[endpoint] > 0 for endpoint in _ENDPOINT_NAMES
        },
    }


def _analyze_raw_action(
    *,
    raw_action_index: int,
    action: EditAction,
    slot: Mapping[str, Any],
    plan_world: Mapping[str, Any],
    world: SparkWorld,
    compressor: _BehaviorCachedCompressor,
    parent: dsl.Expr,
    parent_result: CompressionResult,
    lineages: Sequence[LineageRecord],
) -> dict[str, Any]:
    parsed = _parsed_action(action)
    closure_row = spark_closure._analyze_factual_slot(
        slot=slot,
        parsed=parsed,
        plan_world=plan_world,
        world=world,
        compressor=compressor,  # type: ignore[arg-type]
        parent=parent,
        parent_result=parent_result,
        lineages=lineages,
    )
    flags = _endpoint_flags(closure_row)
    lineage = _lineage_by_action(lineages, action)
    if flags["K1"] != (lineage is not None):
        raise OpportunityMapError("K1 differs from control-ready lineage membership")

    row: dict[str, Any] = {
        "raw_action_index": raw_action_index,
        "action": parsed.to_dict(),
        "action_hash": action.action_hash,
        "expected_old_subtree_hash": action.expected_old_subtree_hash,
        "control_ready_lineage_member": flags["K1"],
        "lineage_failure": closure_row.get("lineage_failure"),
        "endpoint_flags": flags,
    }
    if lineage is None:
        return row

    _audit_replacement_pool(world=world, parent=parent, lineage=lineage)
    child_result = compressor.run(
        lineage.child_ast,
        max_rounds=spark_closure.CLOSURE_MAX_ROUNDS,
    )
    replacement_outcomes: list[dict[str, Any]] = []
    for pool_index, replacement in enumerate(lineage.matched_replacements):
        replacement_result = compressor.run(
            replacement.child_ast,
            max_rounds=spark_closure.CLOSURE_MAX_ROUNDS,
        )
        replacement_outcomes.append(
            {
                "pool_index": pool_index,
                "motif_id": replacement.motif_id,
                "motif_stratum": replacement.motif_stratum,
                "motif_complexity_bucket": list(
                    motif_by_id(replacement.motif_id).complexity_bucket
                ),
                "same_frame_as_focal": True,
                "replay_validated": True,
                "child_canonical_hash": replacement.child_canonical_hash,
                "child_behavior_hash": replacement.child_behavior_hash,
                "reaches_endpoint": replacement_result.exact_identification,
                "N_T": replacement_result.N_T,
            }
        )
    if len(replacement_outcomes) < 2:
        raise OpportunityMapError("K1 lineage has fewer than two controls")
    formal_successes = [
        bool(item["reaches_endpoint"]) for item in replacement_outcomes[:2]
    ]
    formal_first_two_behavior_distinct = (
        replacement_outcomes[0]["child_behavior_hash"]
        != replacement_outcomes[1]["child_behavior_hash"]
    )
    # The formal experiment froze the first two structurally matched controls,
    # not the first two *semantically distinct* controls.  Preserve that exact
    # estimand even when both controls happen to share a behavior, and expose
    # the duplication below as a post-hoc robustness diagnostic.
    if flags["K4"] != bool(flags["K3"] and not any(formal_successes)):
        raise OpportunityMapError("formal K4 differs from the frozen first-two controls")
    success_count = sum(
        bool(item["reaches_endpoint"]) for item in replacement_outcomes
    )
    failure_count = len(replacement_outcomes) - success_count
    full_pool_all_fail = success_count == 0
    outcome_by_behavior: dict[str, bool] = {}
    for replacement in replacement_outcomes:
        behavior_hash = str(replacement["child_behavior_hash"])
        outcome = bool(replacement["reaches_endpoint"])
        previous = outcome_by_behavior.setdefault(behavior_hash, outcome)
        if previous != outcome:
            raise OpportunityMapError(
                "behavior-equivalent replacements have different endpoints"
            )
    unique_behavior_success_count = sum(outcome_by_behavior.values())
    unique_behavior_failure_count = (
        len(outcome_by_behavior) - unique_behavior_success_count
    )
    row.update(
        {
            "lineage_hash": lineage.lineage_hash,
            "child_canonical_hash": lineage.child_canonical_hash,
            "child_behavior_hash": lineage.child_behavior_hash,
            "child_direct_hit": spark_closure._direct_hit(compressor, lineage.child_ast),
            "child_trajectory": _trajectory_projection(child_result),
            "formal_counterfactual_bundle_sha256": _formal_bundle_id(lineage),
            "full_pool_counterfactual_bundle_sha256": _full_pool_bundle_id(lineage),
            "formal_first_two_replacements": replacement_outcomes[:2],
            "formal_first_two_behavior_distinct": (
                formal_first_two_behavior_distinct
            ),
            "same_frame_control_audit": {
                "all_replacements_same_frame": True,
                "all_replacements_same_stratum": True,
                "all_replacements_same_complexity_bucket": True,
                "all_replacements_replayed": True,
                "focal_motif_stratum": lineage.motif_stratum,
                "focal_motif_complexity_bucket": list(
                    lineage.motif_complexity_bucket
                ),
                "operation": lineage.action.operation,
                "path": list(lineage.action.path),
                "binary_operator": lineage.action.binary_operator,
                "motif_side": lineage.action.motif_side,
            },
            "full_replacement_pool_robustness": {
                "pool_size": len(replacement_outcomes),
                "unique_behavior_pool_size": len(outcome_by_behavior),
                "endpoint_success_count": success_count,
                "endpoint_failure_count": failure_count,
                "endpoint_closure_fraction": success_count
                / len(replacement_outcomes),
                "endpoint_failure_fraction": failure_count
                / len(replacement_outcomes),
                "unique_behavior_endpoint_success_count": (
                    unique_behavior_success_count
                ),
                "unique_behavior_endpoint_failure_count": (
                    unique_behavior_failure_count
                ),
                "unique_behavior_endpoint_closure_fraction": (
                    unique_behavior_success_count / len(outcome_by_behavior)
                ),
                "unique_behavior_endpoint_failure_fraction": (
                    unique_behavior_failure_count / len(outcome_by_behavior)
                ),
                "formal_first_two_behavior_distinct": (
                    formal_first_two_behavior_distinct
                ),
                "all_available_replacements_fail_endpoint": full_pool_all_fail,
                "k3_and_all_available_replacements_fail_endpoint": bool(
                    flags["K3"] and full_pool_all_fail
                ),
                "ordered_outcome_sha256": _sha256_json(replacement_outcomes),
                "replacement_outcomes": replacement_outcomes,
            },
        }
    )
    return row


def _aggregate_landscape(worlds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    slots = [slot for world in worlds for slot in world["slots"]]
    actions = [action for slot in slots for action in slot["actions"]]
    if (
        len(worlds) != EXPECTED_WORLD_COUNT
        or len(slots) != EXPECTED_SLOT_COUNT
        or len(actions) != EXPECTED_RAW_ACTION_COUNT
        or any(slot["counts"]["universe_counts"]["raw_syntactic_actions"] != 10 for slot in slots)
    ):
        raise OpportunityMapError("opportunity landscape is not the frozen 32x3x10 grid")

    raw_endpoint_counts = {
        endpoint: sum(bool(action["endpoint_flags"][endpoint]) for action in actions)
        for endpoint in _ENDPOINT_NAMES
    }
    behavior_endpoint_counts = {
        endpoint: sum(
            int(slot["counts"]["endpoint_counts"]["unique_child_behaviors"][endpoint])
            for slot in slots
        )
        for endpoint in _ENDPOINT_NAMES
    }
    bundle_endpoint_counts = {
        endpoint: sum(
            int(
                slot["counts"]["endpoint_counts"]
                ["unique_formal_counterfactual_bundles"][endpoint]
            )
            for slot in slots
        )
        for endpoint in _ENDPOINT_NAMES
    }
    slot_opportunity_counts = {
        endpoint: sum(
            bool(slot["counts"]["endpoint_opportunity"][endpoint]) for slot in slots
        )
        for endpoint in _ENDPOINT_NAMES
    }
    world_opportunity_counts = {
        endpoint: sum(
            any(
                slot["counts"]["endpoint_opportunity"][endpoint]
                for slot in world["slots"]
            )
            for world in worlds
        )
        for endpoint in _ENDPOINT_NAMES
    }

    strata: dict[str, Any] = {}
    for stratum in spark_closure.MOTIF_STRATA:
        stratum_slots = [slot for slot in slots if slot["motif_stratum"] == stratum]
        stratum_actions = [
            action for slot in stratum_slots for action in slot["actions"]
        ]
        strata[stratum] = {
            "slot_count": len(stratum_slots),
            "raw_action_count": len(stratum_actions),
            "control_ready_action_count": sum(
                bool(action["endpoint_flags"]["K1"])
                for action in stratum_actions
            ),
            "raw_action_endpoint_counts": {
                endpoint: sum(
                    bool(action["endpoint_flags"][endpoint])
                    for action in stratum_actions
                )
                for endpoint in _ENDPOINT_NAMES
            },
            "slot_opportunity_counts": {
                endpoint: sum(
                    bool(slot["counts"]["endpoint_opportunity"][endpoint])
                    for slot in stratum_slots
                )
                for endpoint in _ENDPOINT_NAMES
            },
        }
        if len(stratum_slots) != 24 or len(stratum_actions) != 240:
            raise OpportunityMapError("motif-stratum opportunity denominator drifted")

    full_pool_robust_actions = sum(
        bool(
            action.get("full_replacement_pool_robustness", {}).get(
                "k3_and_all_available_replacements_fail_endpoint"
            )
        )
        for action in actions
    )
    return {
        "world_count": len(worlds),
        "slot_count": len(slots),
        "raw_syntactic_action_count": len(actions),
        "control_ready_action_count": raw_endpoint_counts["K1"],
        "slot_local_unique_child_behavior_count": sum(
            int(slot["counts"]["universe_counts"]["unique_child_behaviors"])
            for slot in slots
        ),
        "slot_local_unique_formal_counterfactual_bundle_count": sum(
            int(
                slot["counts"]["universe_counts"]
                ["unique_formal_counterfactual_bundles"]
            )
            for slot in slots
        ),
        "raw_action_endpoint_counts": raw_endpoint_counts,
        "slot_local_unique_child_behavior_endpoint_counts": behavior_endpoint_counts,
        "slot_local_unique_formal_counterfactual_bundle_endpoint_counts": (
            bundle_endpoint_counts
        ),
        "slot_opportunity_counts": slot_opportunity_counts,
        "world_opportunity_counts": world_opportunity_counts,
        "full_pool_robust_k3_action_count": full_pool_robust_actions,
        "formal_k4_but_not_full_pool_robust_action_count": sum(
            bool(action["endpoint_flags"]["K4"])
            and not bool(
                action.get("full_replacement_pool_robustness", {}).get(
                    "k3_and_all_available_replacements_fail_endpoint"
                )
            )
            for action in actions
        ),
        "by_motif_stratum": strata,
    }


def build_action_opportunity_landscape(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Exhaust the 32x3x10 action geometry without reading model outputs.

    The function deliberately accepts only ``plan``.  This keeps the action
    landscape independent of which action any model happened to select.
    """

    diagnostic_manifest = _current_source_manifest_sha256()
    slots, _routes, _world_digests, _slot_digests = _validate_sealed_plan(plan)
    slots_by_world: dict[int, list[Mapping[str, Any]]] = {}
    for slot in slots:
        slots_by_world.setdefault(int(slot["world_seed"]), []).append(slot)

    world_rows: list[dict[str, Any]] = []
    total_hits = 0
    total_misses = 0
    target_namespace = str(plan["target_seed_namespace"])
    for plan_world in plan["worlds"]:
        world_seed = int(plan_world["world_seed"])
        target_seed = spark_closure._target_seed_for_namespace(
            world_seed,
            target_namespace,
        )
        world = spark_closure.generate_spark_world(
            world_seed,
            target_seed=target_seed,
        )
        parent = select_parent(world)
        actual_d0 = [
            {"point": list(example.point), "label": example.label}
            for example in world.train
        ]
        if (
            dsl.canonical_hash(parent) != plan_world["parent_canonical_hash"]
            or dsl.to_sexpr(parent) != plan_world["parent"]
            or actual_d0 != plan_world["D0"]
        ):
            raise OpportunityMapError("diagnostic world differs from its sealed public plan")

        compressor = _BehaviorCachedCompressor(SparkCompressor(world))
        parent_result = compressor.run(
            parent,
            max_rounds=spark_closure.CLOSURE_MAX_ROUNDS,
        )
        lineages = spark_closure.enumerate_reachable_children(world)
        slot_rows: list[dict[str, Any]] = []
        for slot in slots_by_world[world_seed]:
            raw_actions = enumerate_raw_slot_actions(world, str(slot["motif_id"]))
            action_rows = [
                _analyze_raw_action(
                    raw_action_index=index,
                    action=action,
                    slot=slot,
                    plan_world=plan_world,
                    world=world,
                    compressor=compressor,
                    parent=parent,
                    parent_result=parent_result,
                    lineages=lineages,
                )
                for index, action in enumerate(raw_actions)
            ]
            counts = _representation_counts(action_rows)
            slot_rows.append(
                {
                    "serial_index": slot["serial_index"],
                    "slot_id": slot["slot_id"],
                    "world_index": slot["world_index"],
                    "world_seed": slot["world_seed"],
                    "slot_index": slot["slot_index"],
                    "motif_id": slot["motif_id"],
                    "motif_stratum": slot["motif_stratum"],
                    "counts": counts,
                    "actions": action_rows,
                }
            )
        total_hits += compressor.cache_hits
        total_misses += compressor.cache_misses
        world_rows.append(
            {
                "world_index": plan_world["world_index"],
                "world_seed": world_seed,
                "world_hash": world.world_hash,
                "target_index": world.target_index,
                "target_canonical_hash": dsl.canonical_hash(world.target),
                "target_seed_namespace_sha256": spark_closure._target_seed_digest(
                    world_seed,
                    namespace=target_namespace,
                ),
                "parent_canonical_hash": dsl.canonical_hash(parent),
                "parent_trajectory": _trajectory_projection(parent_result),
                "slots": slot_rows,
            }
        )

    aggregate = _aggregate_landscape(world_rows)
    _assert_stable_source_manifest(diagnostic_manifest)
    without_digest: dict[str, Any] = {
        "schema_version": OPPORTUNITY_MAP_SCHEMA_VERSION,
        "kind": OPPORTUNITY_LANDSCAPE_KIND,
        "diagnostic_id": OPPORTUNITY_DIAGNOSTIC_ID,
        "protocol_id": plan["protocol_id"],
        "plan_sha256": plan["plan_sha256"],
        "public_identity_sha256": plan["public_identity_sha256"],
        "diagnostic_source_manifest_sha256": diagnostic_manifest,
        "sealed_input_identity": _sealed_input_identity(),
        "post_hoc_explanatory_only": True,
        "model_outputs_read": False,
        "provider_calls_made": 0,
        "formal_analysis_mutated": False,
        "universe_definition": {
            "worlds": 32,
            "assigned_motif_slots_per_world": 3,
            "raw_actions_per_slot": 10,
            "raw_action_grammar": (
                "two predicate operand paths times replace plus canonical add/sub/mul "
                "wrap frames"
            ),
            "K1": "control-ready membership in enumerate_reachable_children",
            "K2_K3_K4": "existing spark_closure layered M/D/R endpoint functions",
            "formal_K4_controls": "matched_replacements[:2] in frozen order",
            "child_semantic_identity": "complete-domain child behavior hash",
            "counterfactual_bundle_identity": (
                "child behavior plus ordered first-two replacement behaviors"
            ),
            "deduplication_scope": "within one frozen world/motif slot",
        },
        "aggregate": aggregate,
        "compressor_cache": {
            "key": "world-local complete-domain behavior hash and max rounds",
            "hits": total_hits,
            "misses": total_misses,
        },
        "worlds": world_rows,
    }
    return {
        **without_digest,
        "landscape_sha256": _sha256_json(without_digest),
    }


def _validate_landscape(
    plan: Mapping[str, Any], landscape: Mapping[str, Any]
) -> None:
    unsigned = {
        key: value for key, value in landscape.items() if key != "landscape_sha256"
    }
    if (
        landscape.get("schema_version") != OPPORTUNITY_MAP_SCHEMA_VERSION
        or landscape.get("kind") != OPPORTUNITY_LANDSCAPE_KIND
        or landscape.get("diagnostic_id") != OPPORTUNITY_DIAGNOSTIC_ID
        or landscape.get("plan_sha256") != plan.get("plan_sha256")
        or landscape.get("sealed_input_identity") != _sealed_input_identity()
        or landscape.get("landscape_sha256") != _sha256_json(unsigned)
        or landscape.get("post_hoc_explanatory_only") is not True
        or landscape.get("model_outputs_read") is not False
        or landscape.get("provider_calls_made") != 0
    ):
        raise OpportunityMapError("opportunity landscape is malformed or tampered")


def _landscape_slot_index(
    landscape: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    worlds = landscape.get("worlds")
    if not isinstance(worlds, list):
        raise OpportunityMapError("opportunity landscape worlds are malformed")
    rows: dict[str, Mapping[str, Any]] = {}
    for world in worlds:
        if not isinstance(world, Mapping) or not isinstance(world.get("slots"), list):
            raise OpportunityMapError("opportunity landscape world is malformed")
        for slot in world["slots"]:
            if not isinstance(slot, Mapping) or not isinstance(slot.get("slot_id"), str):
                raise OpportunityMapError("opportunity landscape slot is malformed")
            slot_id = str(slot["slot_id"])
            if slot_id in rows:
                raise OpportunityMapError("opportunity landscape duplicates a slot")
            rows[slot_id] = slot
    if len(rows) != EXPECTED_SLOT_COUNT:
        raise OpportunityMapError("opportunity landscape does not contain 96 slots")
    return rows


def _analysis_world_index(
    analysis: Mapping[str, Any], arm_id: str
) -> dict[int, Mapping[str, Any]]:
    try:
        worlds = analysis["joint_analysis"]["arms"][arm_id]["worlds"]
    except (KeyError, TypeError) as exc:
        raise OpportunityMapError("sealed analysis arm worlds are malformed") from exc
    if not isinstance(worlds, list):
        raise OpportunityMapError("sealed analysis arm worlds are malformed")
    result = {
        int(world["world_seed"]): world
        for world in worlds
        if isinstance(world, Mapping) and type(world.get("world_seed")) is int
    }
    if len(result) != EXPECTED_WORLD_COUNT:
        raise OpportunityMapError("sealed analysis arm does not contain 32 worlds")
    return result


def _action_key(value: Mapping[str, Any]) -> str:
    return _sha256_json(dict(value))


def _slot_action_index(slot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    actions = slot.get("actions")
    if not isinstance(actions, list) or len(actions) != RAW_ACTIONS_PER_SLOT:
        raise OpportunityMapError("landscape slot does not contain ten actions")
    indexed: dict[str, Mapping[str, Any]] = {}
    for row in actions:
        if not isinstance(row, Mapping) or not isinstance(row.get("action"), Mapping):
            raise OpportunityMapError("landscape action row is malformed")
        key = _action_key(row["action"])
        if key in indexed:
            raise OpportunityMapError("landscape action grammar is duplicated")
        indexed[key] = row
    return indexed


def _semantic_opportunity_sets(
    slot: Mapping[str, Any],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    actions = slot["actions"]
    behaviors = {
        endpoint: {
            str(row["child_behavior_hash"])
            for row in actions
            if row["endpoint_flags"][endpoint]
        }
        for endpoint in _ENDPOINT_NAMES
    }
    bundles = {
        endpoint: {
            str(row["formal_counterfactual_bundle_sha256"])
            for row in actions
            if row["endpoint_flags"][endpoint]
        }
        for endpoint in _ENDPOINT_NAMES
    }
    return behaviors, bundles


def _sealed_slot_rows(
    analysis_world: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    rows = analysis_world.get("slot_results")
    if not isinstance(rows, list):
        raise OpportunityMapError("sealed analysis slot rows are malformed")
    result = {
        str(row["slot_id"]): row
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("slot_id"), str)
    }
    if len(result) != spark_cross_model.CROSS_MODEL_CALLS_PER_WORLD:
        raise OpportunityMapError("sealed analysis world does not contain three slots")
    return result


def _reproduce_world_qualifying_slots(
    analysis_world: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    expected = analysis_world.get("layered_qualifying_slot_ids")
    if not isinstance(expected, Mapping):
        raise OpportunityMapError("sealed qualifying-slot map is malformed")
    reproduced = {
        layer: [
            row["slot_id"]
            for row in rows
            if row["formal_layer_flags"][layer]
        ]
        for layer in ("L", "M", "D", "R", "S")
    }
    reproduced["weak_at_least_one_replacement_failure"] = [
        row["slot_id"]
        for row in rows
        if row["formal_layer_flags"][
            "weak_at_least_one_replacement_failure"
        ]
    ]
    if dict(expected) != reproduced:
        raise OpportunityMapError(
            "model overlay does not reproduce sealed qualifying slot ids"
        )


def _opportunity_decomposition(
    rows: Sequence[Mapping[str, Any]],
    hit_field: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for endpoint in _ENDPOINT_NAMES:
        opportunities = sum(bool(row["slot_opportunity"][endpoint]) for row in rows)
        hits = sum(bool(row[hit_field][endpoint]) for row in rows)
        if hits > opportunities:
            raise OpportunityMapError("model hit count exceeds available opportunities")
        result[endpoint] = {
            "slot_denominator": len(rows),
            "opportunity_slots": opportunities,
            "hit_slots": hits,
            "missed_opportunity_slots": opportunities - hits,
            "no_opportunity_slots": len(rows) - opportunities,
            "conditional_hit_rate_given_opportunity": _fraction(hits, opportunities),
        }
    return result


def _world_opportunity_decomposition(
    rows: Sequence[Mapping[str, Any]],
    hit_field: str,
) -> dict[str, Any]:
    by_world: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_world.setdefault(int(row["world_seed"]), []).append(row)
    if len(by_world) != EXPECTED_WORLD_COUNT:
        raise OpportunityMapError("model overlay world denominator is not 32")
    result: dict[str, Any] = {}
    for endpoint in _ENDPOINT_NAMES:
        opportunity_seeds = [
            seed
            for seed, world_rows in by_world.items()
            if any(row["slot_opportunity"][endpoint] for row in world_rows)
        ]
        hit_seeds = [
            seed
            for seed, world_rows in by_world.items()
            if any(row[hit_field][endpoint] for row in world_rows)
        ]
        opportunity_set = set(opportunity_seeds)
        hit_set = set(hit_seeds)
        if not hit_set.issubset(opportunity_set):
            raise OpportunityMapError("model world hit lacks a world opportunity")
        missed_seeds = [seed for seed in opportunity_seeds if seed not in hit_set]
        no_opportunity_seeds = [
            seed for seed in by_world if seed not in opportunity_set
        ]
        result[endpoint] = {
            "world_denominator": len(by_world),
            "opportunity_world_count": len(opportunity_seeds),
            "hit_world_count": len(hit_seeds),
            "missed_opportunity_world_count": len(missed_seeds),
            "no_opportunity_world_count": len(no_opportunity_seeds),
            "conditional_hit_rate_given_opportunity_world": _fraction(
                len(hit_seeds), len(opportunity_seeds)
            ),
            "opportunity_world_seeds": opportunity_seeds,
            "hit_world_seeds": hit_seeds,
            "missed_opportunity_world_seeds": missed_seeds,
            "no_opportunity_world_seeds": no_opportunity_seeds,
        }
    return result


def overlay_sealed_model_actions(
    plan: Mapping[str, Any],
    bundle: Mapping[str, Any],
    analysis: Mapping[str, Any],
    landscape: Mapping[str, Any],
) -> dict[str, Any]:
    """Overlay sealed model choices and prove exact K1-K4 reproduction."""

    diagnostic_manifest = _current_source_manifest_sha256()
    artifacts_by_arm = _validate_sealed_triple(plan, bundle, analysis)
    _validate_landscape(plan, landscape)
    if landscape.get("diagnostic_source_manifest_sha256") != diagnostic_manifest:
        raise OpportunityMapError(
            "landscape was computed by a different diagnostic source manifest"
        )
    landscape_slots = _landscape_slot_index(landscape)
    plan_slots = {str(slot["slot_id"]): slot for slot in plan["slots"]}

    arm_rows: dict[str, Any] = {}
    for arm_id in spark_cross_model.CROSS_MODEL_ARM_IDS:
        analysis_worlds = _analysis_world_index(analysis, arm_id)
        records = artifacts_by_arm[arm_id]["records"]
        overlay_rows: list[dict[str, Any]] = []
        overlay_by_world: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            slot_id = str(record["slot_id"])
            slot = landscape_slots[slot_id]
            planned = plan_slots[slot_id]
            if (
                record["serial_index"] != planned["serial_index"]
                or record["world_seed"] != planned["world_seed"]
                or record["motif_id"] != planned["motif_id"]
            ):
                raise OpportunityMapError("overlay record identity differs from its slot")
            parsed = spark_closure._parsed_action_fields(record.get("action"))
            selected: Mapping[str, Any] | None = None
            if parsed is not None and not parsed.is_no_op:
                selected = _slot_action_index(slot).get(_action_key(parsed.to_dict()))

            world_seed = int(record["world_seed"])
            sealed_world = analysis_worlds[world_seed]
            sealed_slot = _sealed_slot_rows(sealed_world)[slot_id]
            sealed_layer = spark_closure._layered_slot_endpoints(sealed_slot)
            sealed_flags = {
                endpoint: bool(sealed_layer[layer])
                for endpoint, layer in _K_TO_LAYER.items()
            }
            selected_flags = {
                endpoint: bool(
                    selected is not None and selected["endpoint_flags"][endpoint]
                )
                for endpoint in _ENDPOINT_NAMES
            }
            if selected_flags != sealed_flags:
                raise OpportunityMapError(
                    "landscape overlay does not reproduce a sealed slot endpoint"
                )

            behavior_sets, bundle_sets = _semantic_opportunity_sets(slot)
            selected_behavior = (
                str(selected["child_behavior_hash"])
                if selected is not None and selected_flags["K1"]
                else None
            )
            selected_bundle = (
                str(selected["formal_counterfactual_bundle_sha256"])
                if selected is not None and selected_flags["K1"]
                else None
            )
            layer_flags = {
                layer: bool(sealed_layer[layer]) for layer in ("L", "M", "D", "R", "S")
            }
            layer_flags["weak_at_least_one_replacement_failure"] = bool(
                sealed_layer["weak_at_least_one_replacement_failure"]
            )
            row = {
                "serial_index": record["serial_index"],
                "slot_id": slot_id,
                "world_seed": world_seed,
                "slot_index": record["slot_index"],
                "motif_id": record["motif_id"],
                "action_parse_valid": record["action_parse_valid"],
                "selected_action": record.get("action"),
                "selected_raw_action_index": (
                    selected.get("raw_action_index") if selected is not None else None
                ),
                "selected_control_ready_lineage": selected_flags["K1"],
                "selected_child_behavior_hash": selected_behavior,
                "selected_formal_counterfactual_bundle_sha256": selected_bundle,
                "slot_opportunity": dict(slot["counts"]["endpoint_opportunity"]),
                "exact_action_hit": selected_flags,
                "child_semantic_hit": {
                    endpoint: selected_behavior is not None
                    and selected_behavior in behavior_sets[endpoint]
                    for endpoint in _ENDPOINT_NAMES
                },
                "formal_counterfactual_bundle_hit": {
                    endpoint: selected_bundle is not None
                    and selected_bundle in bundle_sets[endpoint]
                    for endpoint in _ENDPOINT_NAMES
                },
                "formal_layer_flags": layer_flags,
            }
            overlay_rows.append(row)
            overlay_by_world.setdefault(world_seed, []).append(row)

        qualifying_reproduced = True
        for world_seed, rows in overlay_by_world.items():
            _reproduce_world_qualifying_slots(analysis_worlds[world_seed], rows)
        reproduced_world_counts = {
            endpoint: sum(
                any(row["exact_action_hit"][endpoint] for row in rows)
                for rows in overlay_by_world.values()
            )
            for endpoint in _ENDPOINT_NAMES
        }
        sealed_arm = analysis["joint_analysis"]["arms"][arm_id]
        if reproduced_world_counts != sealed_arm["world_counts_K"]:
            raise OpportunityMapError("overlay does not reproduce sealed arm K1-K4")

        arm_rows[arm_id] = {
            "record_count": len(overlay_rows),
            "sealed_world_counts_K": dict(sealed_arm["world_counts_K"]),
            "reproduced_world_counts_K": reproduced_world_counts,
            "sealed_classification": sealed_arm["classification"],
            "formal_K1_K4_reproduced": True,
            "formal_qualifying_slot_ids_reproduced": qualifying_reproduced,
            "selected_slot_endpoint_counts": {
                endpoint: sum(row["exact_action_hit"][endpoint] for row in overlay_rows)
                for endpoint in _ENDPOINT_NAMES
            },
            "opportunity_decomposition": {
                "exact_action": _opportunity_decomposition(
                    overlay_rows, "exact_action_hit"
                ),
                "child_semantic": _opportunity_decomposition(
                    overlay_rows, "child_semantic_hit"
                ),
                "formal_counterfactual_bundle": _opportunity_decomposition(
                    overlay_rows, "formal_counterfactual_bundle_hit"
                ),
            },
            "world_opportunity_decomposition": {
                "exact_action": _world_opportunity_decomposition(
                    overlay_rows, "exact_action_hit"
                ),
                "child_semantic": _world_opportunity_decomposition(
                    overlay_rows, "child_semantic_hit"
                ),
                "formal_counterfactual_bundle": _world_opportunity_decomposition(
                    overlay_rows, "formal_counterfactual_bundle_hit"
                ),
            },
            "slots": overlay_rows,
        }
    _assert_stable_source_manifest(diagnostic_manifest)
    without_digest = {
        "schema_version": OPPORTUNITY_MAP_SCHEMA_VERSION,
        "kind": OPPORTUNITY_OVERLAY_KIND,
        "diagnostic_id": OPPORTUNITY_DIAGNOSTIC_ID,
        "protocol_id": plan["protocol_id"],
        "plan_sha256": plan["plan_sha256"],
        "generation_bundle_sha256": bundle["bundle_sha256"],
        "analysis_sha256": analysis["analysis_sha256"],
        "landscape_sha256": landscape["landscape_sha256"],
        "diagnostic_source_manifest_sha256": diagnostic_manifest,
        "post_hoc_explanatory_only": True,
        "provider_calls_made": 0,
        "formal_analysis_mutated": False,
        "formal_joint_classification_unchanged": analysis["joint_analysis"]
        ["joint_classification"],
        "arms": arm_rows,
    }
    return {
        **without_digest,
        "overlay_sha256": _sha256_json(without_digest),
    }


def _focus_diagnostics(
    landscape: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    """Summarize the two result-exposed questions frozen in plan section 21."""

    landscape_worlds = {
        int(world["world_seed"]): world for world in landscape["worlds"]
    }
    overlay_slots_by_arm = {
        arm_id: {
            str(row["slot_id"]): row
            for row in overlay["arms"][arm_id]["slots"]
        }
        for arm_id in spark_cross_model.CROSS_MODEL_ARM_IDS
    }
    k3_worlds_by_arm = {
        arm_id: {
            int(row["world_seed"])
            for row in overlay["arms"][arm_id]["slots"]
            if row["exact_action_hit"]["K3"]
        }
        for arm_id in spark_cross_model.CROSS_MODEL_ARM_IDS
    }
    common_k3 = set.intersection(
        *(set(values) for values in k3_worlds_by_arm.values())
    )
    if common_k3 != set(_SEALED_COMMON_K3_WORLD_SEEDS):
        raise OpportunityMapError(
            "post-hoc focus does not reproduce the sealed common K3 worlds"
        )

    common_rows: list[dict[str, Any]] = []
    for world_seed in _SEALED_COMMON_K3_WORLD_SEEDS:
        world = landscape_worlds[world_seed]
        slots = {str(slot["slot_id"]): slot for slot in world["slots"]}
        k4_slot_ids = [
            slot_id
            for slot_id, slot in slots.items()
            if slot["counts"]["endpoint_opportunity"]["K4"]
        ]
        selected_k3_slot_ids_by_arm: dict[str, list[str]] = {}
        selected_k3_details_by_arm: dict[str, list[dict[str, Any]]] = {}
        selected_k3_union: set[str] = set()
        for arm_id in spark_cross_model.CROSS_MODEL_ARM_IDS:
            rows = [
                row
                for row in overlay_slots_by_arm[arm_id].values()
                if int(row["world_seed"]) == world_seed
                and row["exact_action_hit"]["K3"]
            ]
            selected_k3_slot_ids_by_arm[arm_id] = [
                str(row["slot_id"]) for row in rows
            ]
            selected_k3_union.update(selected_k3_slot_ids_by_arm[arm_id])
            details: list[dict[str, Any]] = []
            for row in rows:
                slot = slots[str(row["slot_id"])]
                action_index = row["selected_raw_action_index"]
                if type(action_index) is not int:
                    raise OpportunityMapError("selected K3 action lost its raw index")
                action = slot["actions"][action_index]
                controls = action["formal_first_two_replacements"]
                details.append(
                    {
                        "slot_id": row["slot_id"],
                        "selected_action": row["selected_action"],
                        "child_behavior_hash": row["selected_child_behavior_hash"],
                        "formal_counterfactual_bundle_sha256": row[
                            "selected_formal_counterfactual_bundle_sha256"
                        ],
                        "frozen_control_endpoint_success_count": sum(
                            bool(control["reaches_endpoint"])
                            for control in controls
                        ),
                        "same_slot_has_some_K4_action": str(row["slot_id"])
                        in k4_slot_ids,
                    }
                )
            selected_k3_details_by_arm[arm_id] = details
        if not k4_slot_ids:
            availability = "world_has_no_K4_action"
        elif selected_k3_union.intersection(k4_slot_ids):
            availability = "selected_K3_slot_has_an_alternative_K4_action"
        else:
            availability = "K4_action_exists_only_in_another_slot"
        common_rows.append(
            {
                "world_seed": world_seed,
                "K4_opportunity_slot_ids": k4_slot_ids,
                "availability_diagnosis": availability,
                "selected_K3_slot_ids_by_arm": selected_k3_slot_ids_by_arm,
                "selected_K3_details_by_arm": selected_k3_details_by_arm,
            }
        )

    glm_slots = overlay_slots_by_arm[spark_cross_model.GLM_ARM_ID]
    glm_k4_rows = [
        row for row in glm_slots.values() if row["exact_action_hit"]["K4"]
    ]
    if (
        len(glm_k4_rows) != 1
        or int(glm_k4_rows[0]["world_seed"]) != _SEALED_GLM_K4_WORLD_SEED
    ):
        raise OpportunityMapError("post-hoc focus lost the sealed GLM K4 instance")
    glm_selected = glm_k4_rows[0]
    glm_world = landscape_worlds[_SEALED_GLM_K4_WORLD_SEED]
    glm_slot = next(
        slot
        for slot in glm_world["slots"]
        if slot["slot_id"] == glm_selected["slot_id"]
    )
    k4_actions = [
        action for action in glm_slot["actions"] if action["endpoint_flags"]["K4"]
    ]
    if not k4_actions:
        raise OpportunityMapError("sealed GLM K4 slot has no enumerated K4 action")
    selected_index = glm_selected["selected_raw_action_index"]
    if type(selected_index) is not int:
        raise OpportunityMapError("sealed GLM K4 action lost its raw index")
    selected_action = glm_slot["actions"][selected_index]
    behavior_aliases: dict[str, list[int]] = {}
    path_counts: dict[str, int] = {}
    for action in k4_actions:
        behavior_aliases.setdefault(str(action["child_behavior_hash"]), []).append(
            int(action["raw_action_index"])
        )
        path_key = ".".join(str(value) for value in action["action"]["path"])
        path_counts[path_key] = path_counts.get(path_key, 0) + 1
    same_slot_choices = {
        arm_id: overlay_slots_by_arm[arm_id][str(glm_selected["slot_id"])][
            "selected_action"
        ]
        for arm_id in spark_cross_model.CROSS_MODEL_ARM_IDS
    }
    glm_focus = {
        "world_seed": _SEALED_GLM_K4_WORLD_SEED,
        "slot_id": glm_selected["slot_id"],
        "motif_id": glm_slot["motif_id"],
        "motif_stratum": glm_slot["motif_stratum"],
        "selected_action": glm_selected["selected_action"],
        "raw_K4_action_count": len(k4_actions),
        "unique_K4_child_behavior_count": len(behavior_aliases),
        "unique_K4_counterfactual_bundle_count": len(
            {
                action["formal_counterfactual_bundle_sha256"]
                for action in k4_actions
            }
        ),
        "K4_action_raw_indices_by_child_behavior": behavior_aliases,
        "K4_action_path_counts": path_counts,
        "K4_actions_only_use_path_1_2": set(path_counts) == {"1.2"},
        "selected_formal_first_two_behavior_distinct": selected_action[
            "formal_first_two_behavior_distinct"
        ],
        "selected_child_N_T": selected_action["child_trajectory"]["N_T"],
        "selected_parent_N_T": glm_world["parent_trajectory"]["N_T"],
        "selected_replacement_N_T": [
            row["N_T"] for row in selected_action["formal_first_two_replacements"]
        ],
        "same_slot_selected_actions_by_arm": same_slot_choices,
    }

    opportunity_worlds = int(
        landscape["aggregate"]["world_opportunity_counts"]["K4"]
    )
    formal_replication_arms = [
        arm_id
        for arm_id in spark_cross_model.CROSS_MODEL_ARM_IDS
        if int(overlay["arms"][arm_id]["reproduced_world_counts_K"]["K4"])
        >= 2
    ]
    if opportunity_worlds < 2:
        availability_interpretation = "formal_threshold_blocked_by_grid_availability"
    elif not formal_replication_arms:
        availability_interpretation = (
            "K4_actions_exist_in_at_least_two_worlds_but_no_arm_hits_two_worlds"
        )
    else:
        availability_interpretation = "at_least_one_arm_hits_the_formal_threshold"
    return {
        "common_six_K3_worlds": common_rows,
        "common_six_K3_world_count": len(common_rows),
        "GLM_unique_K4_instance": glm_focus,
        "availability_interpretation": availability_interpretation,
        "formal_result_remains_unchanged": True,
    }


def build_action_opportunity_map(
    plan: Mapping[str, Any],
    bundle: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the complete post-hoc landscape plus sealed model overlay."""

    diagnostic_manifest = _current_source_manifest_sha256()
    # Validate the whole triple before spending time on the exhaustive map.
    _validate_sealed_triple(plan, bundle, analysis)
    landscape = build_action_opportunity_landscape(plan)
    overlay = overlay_sealed_model_actions(plan, bundle, analysis, landscape)
    focus = _focus_diagnostics(landscape, overlay)
    _assert_stable_source_manifest(diagnostic_manifest)
    if (
        landscape["diagnostic_source_manifest_sha256"] != diagnostic_manifest
        or overlay["diagnostic_source_manifest_sha256"] != diagnostic_manifest
    ):
        raise OpportunityMapError("diagnostic components use different source manifests")
    without_digest = {
        "schema_version": OPPORTUNITY_MAP_SCHEMA_VERSION,
        "kind": OPPORTUNITY_MAP_KIND,
        "diagnostic_id": OPPORTUNITY_DIAGNOSTIC_ID,
        "protocol_id": plan["protocol_id"],
        "diagnostic_source_manifest_sha256": diagnostic_manifest,
        "sealed_input_identity": _sealed_input_identity(),
        "source_manifest_policy": {
            "exact_historical_triple_allowlisted_for_offline_diagnostic": True,
            "current_manifest_stable_before_and_after": True,
            "existing_live_generation_barriers_modified": False,
            "existing_joint_analysis_barriers_modified": False,
        },
        "post_hoc_explanatory_only": True,
        "provider_calls_made": 0,
        "formal_analysis_mutated": False,
        "formal_joint_classification_unchanged": analysis["joint_analysis"]
        ["joint_classification"],
        "landscape": landscape,
        "model_overlay": overlay,
        "focus_diagnostics": focus,
        "interpretation_limit": (
            "descriptive action-opportunity decomposition inside the already-opened "
            "finite 32-world DSL benchmark only; it cannot upgrade or replace the "
            "sealed formal classification and supplies no prevalence, model-ranking, "
            "entropy-causation, or human-unknown-discovery inference"
        ),
    }
    return {
        **without_digest,
        "opportunity_map_sha256": _sha256_json(without_digest),
    }


def _read_exact_json(path: str | Path, expected_file_sha256: str) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise OpportunityMapError(f"cannot read sealed artifact {source}") from exc
    if hashlib.sha256(payload).hexdigest() != expected_file_sha256:
        raise OpportunityMapError(
            f"sealed artifact bytes are outside the exact allowlist: {source}"
        )
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OpportunityMapError(f"sealed artifact is invalid JSON: {source}") from exc
    if not isinstance(value, dict):
        raise OpportunityMapError(f"sealed artifact must contain an object: {source}")
    return value


def _emit_json_exclusive_0600(value: Mapping[str, Any], output: str | Path) -> None:
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
        raise OpportunityMapError(f"refusing to overwrite artifact {path}") from exc
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
        # The exclusive file may contain a partial diagnostic.  Preserve it as
        # forensic evidence and never silently retry/overwrite it.
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline post-hoc opportunity map for the exact sealed triad"
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--generations", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    plan = _read_exact_json(args.plan, SEALED_PLAN_FILE_SHA256)
    bundle = _read_exact_json(args.generations, SEALED_GENERATIONS_FILE_SHA256)
    analysis = _read_exact_json(args.analysis, SEALED_ANALYSIS_FILE_SHA256)
    result = build_action_opportunity_map(plan, bundle, analysis)
    _emit_json_exclusive_0600(result, args.output)
    return 0


__all__ = [
    "EXPECTED_RAW_ACTION_COUNT",
    "EXPECTED_SLOT_COUNT",
    "EXPECTED_WORLD_COUNT",
    "OPPORTUNITY_DIAGNOSTIC_ID",
    "OPPORTUNITY_LANDSCAPE_KIND",
    "OPPORTUNITY_MAP_KIND",
    "OPPORTUNITY_MAP_SCHEMA_VERSION",
    "OPPORTUNITY_OVERLAY_KIND",
    "OpportunityMapError",
    "RAW_ACTIONS_PER_SLOT",
    "SEALED_ANALYSIS_SHA256",
    "SEALED_GENERATION_BUNDLE_SHA256",
    "SEALED_PLAN_SHA256",
    "build_action_opportunity_landscape",
    "build_action_opportunity_map",
    "enumerate_raw_slot_actions",
    "main",
    "overlay_sealed_model_actions",
]


if __name__ == "__main__":
    raise SystemExit(main())
