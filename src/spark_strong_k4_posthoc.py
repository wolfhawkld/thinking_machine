"""Offline explanatory diagnostics for the completed fair-choice experiment.

The formal result is an immutable input to this module.  Diagnostics begin
only after the exact complete generation bundle has passed its public barrier,
then read the exact private key and describe already-observed choices.  They do
not call a provider, rescore a formal route, rerun Holm, or create a new
confirmatory classification.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .provenance import PROJECT_ROOT, source_manifest
from . import spark_lineage
from . import spark_strong_k4_benchmark as benchmark
from . import spark_strong_k4_formal as formal


SCHEMA_VERSION = 1
POSTHOC_KIND = "spark-strong-k4-fair-choice-posthoc-diagnostic"
POSTHOC_STATUS = "post_hoc_not_confirmatory"
ARMS = tuple(benchmark.ARMS)
ENDPOINTS = tuple(benchmark.ENDPOINT_NAMES)
ROUTES = tuple(benchmark.CANONICAL_ROUTE_IDS)
STRATA = tuple(spark_lineage.MOTIF_STRATA)
INTERPRETATION_LIMIT = (
    "This outcome-conditioned finite DSL description cannot support shortcut "
    "attribution, natural opportunity prevalence, model ranking, an entropy-causal "
    "claim, or discovery of human-unknown knowledge."
)
PAIRED_SELECTION_INTERPRETATION = (
    "same_raw_action means only that the selected semantic frame did not change "
    "between the factual and sham prompts; it does not establish identical reasoning."
)

_FORMAL_JOINT_LABELS = {
    "all_routes_effect_observed",
    "cross_family_effect_observed",
    "deepseek_family_only_effect_observed",
    "single_route_effect_observed",
    "effect_not_observed_under_frozen_protocol",
    "non_evaluable_incomplete_attempt",
}
_FORMAL_ROUTE_LABELS = {
    "paired_strong_K4_effect_observed",
    "strong_hits_shortcut_compatible",
    "effect_not_observed",
    "model_dsl_interface_failure",
}
_PAIR_ORDINALS = set(range(benchmark.PAIR_COUNT))
_RAW_INDICES = {str(index) for index in range(benchmark.RAW_ACTION_COUNT)}


class FairChoicePosthocError(ValueError):
    """An exact formal input or descriptive diagnostic is malformed."""


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
        raise FairChoicePosthocError(
            "post-hoc values must be finite canonical JSON"
        ) from exc


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


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _exact_keys(value: object, fields: set[str]) -> bool:
    return isinstance(value, Mapping) and set(value) == fields


def _valid_count_map(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and all(isinstance(key, str) and key for key in value)
        and all(_is_nonnegative_int(count) for count in value.values())
    )


def _valid_ordinals(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(type(item) is int and item in _PAIR_ORDINALS for item in value)
        and len(value) == len(set(value))
        and value == sorted(value)
    )


def _read_bound_json(
    path: str | Path,
    expected_file_sha256: str,
    *,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    if not _is_sha256(expected_file_sha256):
        raise FairChoicePosthocError(f"expected {label} SHA-256 is malformed")
    source = Path(path)
    try:
        raw = source.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FairChoicePosthocError(f"cannot read {label} {source}") from exc
    if _sha256_bytes(raw) != expected_file_sha256:
        raise FairChoicePosthocError(f"{label} differs from its exact allowlist")
    if not isinstance(value, dict):
        raise FairChoicePosthocError(f"{label} must contain one JSON object")
    return value, raw


def _validate_formal_analysis_reference(
    analysis: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    expected_plan_file_sha256: str,
    public_manifest: Mapping[str, Any],
    bundle: Mapping[str, Any],
    expected_bundle_file_sha256: str,
) -> dict[str, Any]:
    expected_fields = {
        "schema_version",
        "kind",
        "protocol_id",
        "formal_plan_file_sha256",
        "formal_plan_sha256",
        "generation_bundle_file_sha256",
        "generation_bundle_sha256",
        "public_manifest_sha256",
        "private_key_sha256",
        "current_source_manifest_sha256",
        "route_scores",
        "joint_result",
        "private_key_loaded_after_complete_generation_barrier",
        "joint_analysis_sha256",
    }
    if not isinstance(analysis, Mapping) or set(analysis) != expected_fields:
        raise FairChoicePosthocError("formal analysis uses an unknown schema")
    unsigned = {
        key: value
        for key, value in analysis.items()
        if key != "joint_analysis_sha256"
    }
    if (
        analysis.get("schema_version") != formal.SCHEMA_VERSION
        or analysis.get("kind") != formal.JOINT_ANALYSIS_KIND
        or analysis.get("protocol_id") != benchmark.PROTOCOL_ID
        or analysis.get("formal_plan_file_sha256")
        != expected_plan_file_sha256
        or analysis.get("formal_plan_sha256") != plan["formal_plan_sha256"]
        or analysis.get("generation_bundle_file_sha256")
        != expected_bundle_file_sha256
        or analysis.get("generation_bundle_sha256")
        != bundle["generation_bundle_sha256"]
        or analysis.get("public_manifest_sha256")
        != public_manifest["public_manifest_sha256"]
        or analysis.get("private_key_sha256")
        != plan["file_bindings"]["private_key"]["inner_sha256"]
        or analysis.get("current_source_manifest_sha256")
        != plan["current_source_manifest_sha256"]
        or analysis.get("private_key_loaded_after_complete_generation_barrier")
        is not True
        or analysis.get("joint_analysis_sha256") != _sha256_json(unsigned)
    ):
        raise FairChoicePosthocError("formal analysis identity chain is malformed")

    scores = analysis.get("route_scores")
    if not isinstance(scores, Mapping) or set(scores) != set(ROUTES):
        raise FairChoicePosthocError("formal analysis route scores are incomplete")
    artifacts = {
        artifact["route_id"]: artifact
        for artifact in bundle["route_response_artifacts"]
    }
    for route_id in ROUTES:
        score = scores[route_id]
        if not isinstance(score, Mapping):
            raise FairChoicePosthocError("formal route score must be an object")
        score_unsigned = {
            key: value for key, value in score.items() if key != "score_sha256"
        }
        if (
            score.get("model_id") != route_id
            or score.get("response_artifact_sha256")
            != artifacts[route_id]["response_artifact_sha256"]
            or score.get("public_manifest_sha256")
            != public_manifest["public_manifest_sha256"]
            or score.get("private_key_sha256")
            != plan["file_bindings"]["private_key"]["inner_sha256"]
            or score.get("current_source_manifest_sha256")
            != plan["current_source_manifest_sha256"]
            or score.get("score_sha256") != _sha256_json(score_unsigned)
        ):
            raise FairChoicePosthocError("formal route score identity is malformed")

    joint = analysis.get("joint_result")
    if (
        not isinstance(joint, Mapping)
        or set(joint) != {"joint_classification", "route_classifications", "holm"}
        or joint.get("joint_classification") not in _FORMAL_JOINT_LABELS
    ):
        raise FairChoicePosthocError("formal joint-result reference is malformed")
    route_results = joint.get("route_classifications")
    if not isinstance(route_results, Mapping) or set(route_results) != set(ROUTES):
        raise FairChoicePosthocError("formal route classifications are incomplete")
    labels: dict[str, str] = {}
    for route_id in ROUTES:
        result = route_results[route_id]
        if (
            not isinstance(result, Mapping)
            or result.get("classification") not in _FORMAL_ROUTE_LABELS
        ):
            raise FairChoicePosthocError("formal route classification is malformed")
        labels[route_id] = str(result["classification"])
    return {
        "formal_analysis_file_sha256": None,
        "formal_joint_analysis_sha256": analysis["joint_analysis_sha256"],
        "joint_classification": joint["joint_classification"],
        "route_classifications": labels,
        "classification_recomputed": False,
        "classification_modified": False,
    }


def _frame_label(semantic_action: Mapping[str, Any]) -> str:
    path = semantic_action.get("path")
    if (
        not isinstance(path, list)
        or len(path) != 2
        or any(type(value) is not int for value in path)
    ):
        raise FairChoicePosthocError("semantic action path is malformed")
    path_label = f"({path[0]},{path[1]})"
    operation = semantic_action.get("operation")
    if operation == "replace" and set(semantic_action) == {"operation", "path"}:
        return f"{path_label}:replace"
    if operation == "wrap_binary" and set(semantic_action) == {
        "operation",
        "path",
        "binary_operator",
        "motif_side",
    }:
        return (
            f"{path_label}:{semantic_action['binary_operator']}-"
            f"{semantic_action['motif_side']}"
        )
    raise FairChoicePosthocError("semantic action frame is malformed")


def _path_label(semantic_action: Mapping[str, Any]) -> str:
    path = semantic_action["path"]
    return f"({path[0]},{path[1]})"


def _counter(values: Sequence[object]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _raw_action(
    pair: Mapping[str, Any], arm: str, raw_index: int | None
) -> Mapping[str, Any] | None:
    if raw_index is None:
        return None
    matches = [
        action
        for action in pair["arms"][arm]["actions"]
        if action["raw_action_index"] == raw_index
    ]
    if len(matches) != 1:
        raise FairChoicePosthocError("selected raw action is absent or duplicated")
    return matches[0]


def _derive_selections(
    private_key: Mapping[str, Any], bundle: Mapping[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    artifacts = {
        artifact["route_id"]: artifact
        for artifact in bundle["route_response_artifacts"]
    }
    selections: dict[str, list[dict[str, Any]]] = {}
    for route_id in ROUTES:
        responses = {row["task_id"]: row for row in artifacts[route_id]["tasks"]}
        route_pairs: list[dict[str, Any]] = []
        for pair in private_key["pairs"]:
            arms: dict[str, Any] = {}
            for arm in ARMS:
                task_id = pair["arms"][arm]["task_id"]
                response = responses[task_id]
                expression = response["expression"]
                valid = (
                    response["candidate_format"] == "json_expression"
                    and isinstance(expression, str)
                    and expression in pair["option_to_raw_action"]
                )
                raw_index = (
                    int(pair["option_to_raw_action"][expression])
                    if valid
                    else None
                )
                action = _raw_action(pair, arm, raw_index)
                display_position = (
                    pair["action_order"].index(raw_index)
                    if raw_index is not None
                    else None
                )
                arms[arm] = {
                    "task_id": task_id,
                    "valid": valid,
                    "raw_action_index": raw_index,
                    "display_position": display_position,
                    "action": action,
                }
            route_pairs.append(
                {
                    "pair_ordinal": pair["pair_ordinal"],
                    "pair_id": pair["pair_id"],
                    "arms": arms,
                }
            )
        selections[route_id] = route_pairs
    return selections


def _selection_preferences(
    selections: Mapping[str, Sequence[Mapping[str, Any]]]
) -> dict[str, Any]:
    routes: dict[str, Any] = {}
    for route_id in ROUTES:
        route_pairs = selections[route_id]
        arms_payload: dict[str, Any] = {}
        for arm in ARMS:
            rows = [pair["arms"][arm] for pair in route_pairs]
            valid = [row for row in rows if row["valid"]]
            raw_counts = {
                str(index): sum(row["raw_action_index"] == index for row in valid)
                for index in range(benchmark.RAW_ACTION_COUNT)
            }
            display_counts = {
                str(index): sum(row["display_position"] == index for row in valid)
                for index in range(benchmark.RAW_ACTION_COUNT)
            }
            frames = [
                _frame_label(row["action"]["semantic_action"]) for row in valid
            ]
            paths = [
                _path_label(row["action"]["semantic_action"]) for row in valid
            ]
            arms_payload[arm] = {
                "received_count": len(rows),
                "valid_choice_count": len(valid),
                "invalid_choice_count": len(rows) - len(valid),
                "raw_action_index_counts": raw_counts,
                "display_position_counts": display_counts,
                "path_counts": _counter(paths),
                "frame_counts": _counter(frames),
                "maximum_raw_action_count": max(raw_counts.values()),
            }
        both_valid = [
            pair
            for pair in route_pairs
            if pair["arms"]["factual"]["valid"]
            and pair["arms"]["sham"]["valid"]
        ]
        same_raw = [
            pair["pair_ordinal"]
            for pair in both_valid
            if pair["arms"]["factual"]["raw_action_index"]
            == pair["arms"]["sham"]["raw_action_index"]
        ]
        routes[route_id] = {
            "arms": arms_payload,
            "both_arms_valid_pair_count": len(both_valid),
            "same_raw_action_pair_count": len(same_raw),
            "same_raw_action_pair_ordinals": same_raw,
        }
    return {"routes": routes}


def _baseline_overlaps(
    private_key: Mapping[str, Any],
    selections: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    report = private_key["baseline_report"]
    policies = report["policies"]
    if (
        len(policies) != len(benchmark.BASELINE_POLICY_IDS)
        or [policy["policy_id"] for policy in policies]
        != list(benchmark.BASELINE_POLICY_IDS)
    ):
        raise FairChoicePosthocError("private baseline family is not the frozen 24")
    output: dict[str, Any] = {}
    for route_id in ROUTES:
        route_rows: list[dict[str, Any]] = []
        route_pairs = selections[route_id]
        for policy in policies:
            baseline_by_pair = policy["selected_raw_action_by_pair"]
            if len(baseline_by_pair) != benchmark.PAIR_COUNT:
                raise FairChoicePosthocError("baseline selection rows are incomplete")
            matches = {arm: [] for arm in ARMS}
            both_arm_matches: list[int] = []
            table = {
                "model_and_baseline": [],
                "model_only": [],
                "baseline_only": [],
                "neither": [],
            }
            for pair_index, (model_pair, baseline_pair, private_pair) in enumerate(
                zip(
                    route_pairs,
                    baseline_by_pair,
                    private_key["pairs"],
                    strict=True,
                )
            ):
                if baseline_pair["pair_id"] != private_pair["pair_id"]:
                    raise FairChoicePosthocError("baseline pair order drifted")
                arm_matches: dict[str, bool] = {}
                for arm in ARMS:
                    model_raw = model_pair["arms"][arm]["raw_action_index"]
                    matched = (
                        model_raw is not None
                        and model_raw == baseline_pair[arm]
                    )
                    arm_matches[arm] = matched
                    if matched:
                        matches[arm].append(pair_index)
                if all(arm_matches.values()):
                    both_arm_matches.append(pair_index)

                selected = model_pair["arms"]["factual"]
                action = selected["action"]
                model_hit = bool(
                    selected["valid"]
                    and action is not None
                    and action["endpoint_flags"]["K4_full_pool"]
                )
                baseline_hit = bool(policy["factual_F_by_pair"][pair_index])
                if model_hit and baseline_hit:
                    table["model_and_baseline"].append(pair_index)
                elif model_hit:
                    table["model_only"].append(pair_index)
                elif baseline_hit:
                    table["baseline_only"].append(pair_index)
                else:
                    table["neither"].append(pair_index)
            route_rows.append(
                {
                    "policy_id": policy["policy_id"],
                    "is_B_star": policy["policy_id"]
                    == report["B_star_policy_id"],
                    "factual_action_match_count": len(matches["factual"]),
                    "factual_action_match_pair_ordinals": matches["factual"],
                    "sham_action_match_count": len(matches["sham"]),
                    "sham_action_match_pair_ordinals": matches["sham"],
                    "both_arms_action_match_count": len(both_arm_matches),
                    "both_arms_action_match_pair_ordinals": both_arm_matches,
                    "factual_K4_overlap": {
                        key: {
                            "count": len(ordinals),
                            "pair_ordinals": ordinals,
                        }
                        for key, ordinals in table.items()
                    },
                }
            )
        output[route_id] = {
            "policy_count": len(route_rows),
            "B_star_policy_id": report["B_star_policy_id"],
            "policies": route_rows,
        }
    return {
        "selection_overlap_is_descriptive_not_shortcut_attribution": True,
        "routes": output,
    }


def _strong_hit_concentration(
    private_key: Mapping[str, Any],
    selections: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    routes: dict[str, Any] = {}
    for route_id in ROUTES:
        hit_rows: list[dict[str, Any]] = []
        for private_pair, selected_pair in zip(
            private_key["pairs"], selections[route_id], strict=True
        ):
            selected = selected_pair["arms"]["factual"]
            action = selected["action"]
            if not (
                selected["valid"]
                and action is not None
                and action["endpoint_flags"]["K4_full_pool"]
            ):
                continue
            features = action["public_features"]
            hit_rows.append(
                {
                    "pair_ordinal": private_pair["pair_ordinal"],
                    "pair_id": private_pair["pair_id"],
                    "task_id": selected["task_id"],
                    "construction_stratum": private_pair["world_binding"][
                        "construction_stratum"
                    ],
                    "raw_action_index": selected["raw_action_index"],
                    "display_position": selected["display_position"],
                    "path": _path_label(action["semantic_action"]),
                    "frame": _frame_label(action["semantic_action"]),
                    "child_behavior_hash": features["child_behavior_hash"],
                    "child_behavior_is_constant": features[
                        "child_behavior_is_constant"
                    ],
                    "full_pool_counterfactual_bundle_sha256": action[
                        "full_pool_counterfactual_bundle_sha256"
                    ],
                }
            )
        behavior_counts = _counter(
            [row["child_behavior_hash"] for row in hit_rows]
        )
        routes[route_id] = {
            "factual_K4_hit_count": len(hit_rows),
            "constant_child_hit_count": sum(
                row["child_behavior_is_constant"] for row in hit_rows
            ),
            "nonconstant_child_hit_count": sum(
                not row["child_behavior_is_constant"] for row in hit_rows
            ),
            "unique_child_behavior_count": len(behavior_counts),
            "maximum_child_behavior_count": (
                max(behavior_counts.values()) if behavior_counts else 0
            ),
            "child_behavior_hash_counts": behavior_counts,
            "construction_stratum_counts": _counter(
                [row["construction_stratum"] for row in hit_rows]
            ),
            "raw_action_index_counts": _counter(
                [row["raw_action_index"] for row in hit_rows]
            ),
            "frame_counts": _counter([row["frame"] for row in hit_rows]),
            "counterfactual_bundle_sha256_counts": _counter(
                [
                    row["full_pool_counterfactual_bundle_sha256"]
                    for row in hit_rows
                ]
            ),
            "hit_rows": hit_rows,
        }
    return {"arm": "factual", "endpoint": "K4_full_pool", "routes": routes}


def _opportunity_landscape(
    private_key: Mapping[str, Any],
    selections: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    arm_payload: dict[str, Any] = {}
    per_pair_counts: dict[str, dict[str, list[int]]] = {
        arm: {endpoint: [] for endpoint in ENDPOINTS} for arm in ARMS
    }
    for arm in ARMS:
        endpoint_payload: dict[str, Any] = {}
        for endpoint in ENDPOINTS:
            counts = [
                sum(
                    bool(action["endpoint_flags"][endpoint])
                    for action in pair["arms"][arm]["actions"]
                )
                for pair in private_key["pairs"]
            ]
            per_pair_counts[arm][endpoint] = counts
            raw_counts = {
                str(raw): sum(
                    bool(pair["arms"][arm]["actions"][raw]["endpoint_flags"][endpoint])
                    for pair in private_key["pairs"]
                )
                for raw in range(benchmark.RAW_ACTION_COUNT)
            }
            by_stratum: dict[str, Any] = {}
            for stratum in STRATA:
                stratum_counts = [
                    count
                    for pair, count in zip(
                        private_key["pairs"], counts, strict=True
                    )
                    if pair["world_binding"]["construction_stratum"] == stratum
                ]
                by_stratum[stratum] = {
                    "pair_count": len(stratum_counts),
                    "qualifying_raw_action_count": sum(stratum_counts),
                    "world_with_opportunity_count": sum(
                        count > 0 for count in stratum_counts
                    ),
                    "per_world_opportunity_count_histogram": {
                        str(count): stratum_counts.count(count)
                        for count in range(benchmark.RAW_ACTION_COUNT + 1)
                    },
                }
            endpoint_payload[endpoint] = {
                "qualifying_raw_action_count": sum(counts),
                "world_with_opportunity_count": sum(count > 0 for count in counts),
                "per_world_opportunity_count_histogram": {
                    str(count): counts.count(count)
                    for count in range(benchmark.RAW_ACTION_COUNT + 1)
                },
                "raw_action_index_qualifying_counts": raw_counts,
                "per_pair_qualifying_action_counts": counts,
                "by_construction_stratum": by_stratum,
            }
        arm_payload[arm] = endpoint_payload

    same_frame: dict[str, Any] = {}
    for endpoint in ENDPOINTS:
        table = {"both": 0, "factual_only": 0, "sham_only": 0, "neither": 0}
        for pair in private_key["pairs"]:
            factual = {
                action["raw_action_index"]: action
                for action in pair["arms"]["factual"]["actions"]
            }
            sham = {
                action["raw_action_index"]: action
                for action in pair["arms"]["sham"]["actions"]
            }
            for raw in range(benchmark.RAW_ACTION_COUNT):
                left = bool(factual[raw]["endpoint_flags"][endpoint])
                right = bool(sham[raw]["endpoint_flags"][endpoint])
                if left and right:
                    table["both"] += 1
                elif left:
                    table["factual_only"] += 1
                elif right:
                    table["sham_only"] += 1
                else:
                    table["neither"] += 1
        same_frame[endpoint] = {**table, "frame_count": sum(table.values())}

    overlay: dict[str, Any] = {}
    for route_id in ROUTES:
        arms: dict[str, Any] = {}
        for arm in ARMS:
            endpoints: dict[str, Any] = {}
            for endpoint in ENDPOINTS:
                categories = {
                    "selected_hit": 0,
                    "opportunity_miss": 0,
                    "no_opportunity": 0,
                    "invalid_choice": 0,
                }
                for pair_index, selected_pair in enumerate(selections[route_id]):
                    selected = selected_pair["arms"][arm]
                    action = selected["action"]
                    if not selected["valid"]:
                        categories["invalid_choice"] += 1
                    elif action is not None and action["endpoint_flags"][endpoint]:
                        categories["selected_hit"] += 1
                    elif per_pair_counts[arm][endpoint][pair_index] > 0:
                        categories["opportunity_miss"] += 1
                    else:
                        categories["no_opportunity"] += 1
                endpoints[endpoint] = categories
            arms[arm] = endpoints
        overlay[route_id] = arms
    return {
        "benchmark_arms": arm_payload,
        "same_raw_frame_factual_sham_tables": same_frame,
        "route_selected_opportunity_overlay": overlay,
    }


def _paired_selected_endpoint_decomposition(
    selections: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    routes: dict[str, Any] = {}
    for route_id in ROUTES:
        endpoint_rows: dict[str, Any] = {}
        for endpoint in ENDPOINTS:
            cells = {
                name: {"same_raw_action": [], "different_raw_action": []}
                for name in ("both_hit", "factual_only", "sham_only", "neither")
            }
            invalid: list[int] = []
            for pair in selections[route_id]:
                factual = pair["arms"]["factual"]
                sham = pair["arms"]["sham"]
                ordinal = pair["pair_ordinal"]
                if not factual["valid"] or not sham["valid"]:
                    invalid.append(ordinal)
                    continue
                factual_hit = bool(factual["action"]["endpoint_flags"][endpoint])
                sham_hit = bool(sham["action"]["endpoint_flags"][endpoint])
                if factual_hit and sham_hit:
                    cell = "both_hit"
                elif factual_hit:
                    cell = "factual_only"
                elif sham_hit:
                    cell = "sham_only"
                else:
                    cell = "neither"
                raw_relation = (
                    "same_raw_action"
                    if factual["raw_action_index"] == sham["raw_action_index"]
                    else "different_raw_action"
                )
                cells[cell][raw_relation].append(ordinal)
            endpoint_rows[endpoint] = {
                name: {
                    "count": sum(len(rows) for rows in split.values()),
                    **{
                        relation: {
                            "count": len(ordinals),
                            "pair_ordinals": ordinals,
                        }
                        for relation, ordinals in split.items()
                    },
                }
                for name, split in cells.items()
            }
            endpoint_rows[endpoint]["invalid"] = {
                "count": len(invalid),
                "pair_ordinals": invalid,
            }
        routes[route_id] = endpoint_rows
    return {"interpretation": PAIRED_SELECTION_INTERPRETATION, "routes": routes}


def build_fair_choice_posthoc_diagnostic(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    public_manifest_path: str | Path,
    expected_public_manifest_file_sha256: str,
    generation_bundle_path: str | Path,
    expected_generation_bundle_file_sha256: str,
    private_key_path: str | Path,
    expected_private_key_file_sha256: str,
    formal_analysis_path: str | Path,
    expected_formal_analysis_file_sha256: str,
) -> dict[str, Any]:
    """Build one exact-input, offline, non-confirmatory diagnostic artifact."""

    plan, _plan_raw = _read_bound_json(
        plan_path,
        expected_plan_file_sha256,
        label="formal plan",
    )
    try:
        formal.validate_fair_choice_formal_plan(plan)
    except formal.FairChoiceFormalError as exc:
        raise FairChoicePosthocError("formal plan validation failed") from exc
    if (
        expected_public_manifest_file_sha256
        != plan["file_bindings"]["public_manifest"]["file_sha256"]
        or expected_private_key_file_sha256
        != plan["file_bindings"]["private_key"]["file_sha256"]
    ):
        raise FairChoicePosthocError("public/private allowlist differs from formal plan")
    public_manifest, _public_raw = _read_bound_json(
        public_manifest_path,
        expected_public_manifest_file_sha256,
        label="public manifest",
    )
    try:
        benchmark.validate_public_manifest(public_manifest)
    except benchmark.FairChoiceError as exc:
        raise FairChoicePosthocError("public manifest validation failed") from exc
    if (
        public_manifest["public_manifest_sha256"]
        != plan["file_bindings"]["public_manifest"]["inner_sha256"]
        or public_manifest["current_source_manifest_sha256"]
        != plan["current_source_manifest_sha256"]
        or public_manifest["fair_config_file_sha256"]
        != plan["file_bindings"]["fair_config"]["file_sha256"]
        or public_manifest["private_design_commitment_sha256"]
        != plan["benchmark_binding"]["private_design_commitment_sha256"]
        or _sha256_json(
            [task["task_id"] for task in public_manifest["tasks"]]
        )
        != plan["benchmark_binding"]["public_task_sequence_sha256"]
    ):
        raise FairChoicePosthocError("public manifest differs from formal plan")

    bundle, _bundle_raw = _read_bound_json(
        generation_bundle_path,
        expected_generation_bundle_file_sha256,
        label="generation bundle",
    )
    try:
        formal.validate_fair_choice_generation_bundle(
            plan, public_manifest, bundle
        )
    except formal.FairChoiceFormalError as exc:
        raise FairChoicePosthocError("generation bundle validation failed") from exc
    if (
        bundle["formal_plan_file_sha256"] != expected_plan_file_sha256
        or bundle["public_manifest_file_sha256"]
        != expected_public_manifest_file_sha256
    ):
        raise FairChoicePosthocError("generation bundle exact-file chain drifted")

    analysis, _analysis_raw = _read_bound_json(
        formal_analysis_path,
        expected_formal_analysis_file_sha256,
        label="formal analysis",
    )
    formal_result_reference = _validate_formal_analysis_reference(
        analysis,
        plan=plan,
        expected_plan_file_sha256=expected_plan_file_sha256,
        public_manifest=public_manifest,
        bundle=bundle,
        expected_bundle_file_sha256=expected_generation_bundle_file_sha256,
    )
    formal_result_reference["formal_analysis_file_sha256"] = (
        expected_formal_analysis_file_sha256
    )

    # The exact complete public barrier and formal result were validated above.
    # This is intentionally the first private-key read in the diagnostic path.
    private_key, _private_raw = _read_bound_json(
        private_key_path,
        expected_private_key_file_sha256,
        label="private key",
    )
    try:
        benchmark.validate_private_key(private_key, public_manifest)
    except benchmark.FairChoiceError as exc:
        raise FairChoicePosthocError("private key validation failed") from exc
    if (
        private_key["private_key_sha256"]
        != plan["file_bindings"]["private_key"]["inner_sha256"]
        or formal._public_private_bijection_sha256(public_manifest, private_key)
        != plan["benchmark_binding"]["public_private_task_bijection_sha256"]
    ):
        raise FairChoicePosthocError("private key identity/bijection drifted")

    selections = _derive_selections(private_key, bundle)
    selection_preferences = _selection_preferences(selections)
    baseline_overlaps = _baseline_overlaps(private_key, selections)
    strong_hit_concentration = _strong_hit_concentration(
        private_key, selections
    )
    opportunity_landscape = _opportunity_landscape(private_key, selections)
    paired_selected_endpoint_decomposition = (
        _paired_selected_endpoint_decomposition(selections)
    )
    diagnostic_source_sha256 = source_manifest(PROJECT_ROOT)[
        "source_manifest_sha256"
    ]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": POSTHOC_KIND,
        "protocol_id": benchmark.PROTOCOL_ID,
        "status": POSTHOC_STATUS,
        "post_hoc_explanatory_only": True,
        "evidence": False,
        "designed_after_formal_outcomes_opened": True,
        "provider_calls_made": 0,
        "formal_analysis_mutated": False,
        "formal_analysis_recomputed": False,
        "new_p_values_computed": False,
        "new_classification_labels_created": False,
        "interpretation_limit": INTERPRETATION_LIMIT,
        "historical_formal_source_manifest_sha256": plan[
            "current_source_manifest_sha256"
        ],
        "diagnostic_source_manifest_sha256": diagnostic_source_sha256,
        "input_bindings": {
            "formal_plan": {
                "file_sha256": expected_plan_file_sha256,
                "inner_sha256": plan["formal_plan_sha256"],
            },
            "public_manifest": {
                "file_sha256": expected_public_manifest_file_sha256,
                "inner_sha256": public_manifest["public_manifest_sha256"],
            },
            "generation_bundle": {
                "file_sha256": expected_generation_bundle_file_sha256,
                "inner_sha256": bundle["generation_bundle_sha256"],
            },
            "private_key": {
                "file_sha256": expected_private_key_file_sha256,
                "inner_sha256": private_key["private_key_sha256"],
            },
            "formal_analysis": {
                "file_sha256": expected_formal_analysis_file_sha256,
                "inner_sha256": analysis["joint_analysis_sha256"],
            },
        },
        "formal_result_reference": formal_result_reference,
        "selection_preferences": selection_preferences,
        "baseline_overlaps": baseline_overlaps,
        "strong_hit_concentration": strong_hit_concentration,
        "opportunity_landscape": opportunity_landscape,
        "paired_selected_endpoint_decomposition": (
            paired_selected_endpoint_decomposition
        ),
    }
    artifact = {
        **unsigned,
        "posthoc_diagnostic_sha256": _sha256_json(unsigned),
    }
    validate_fair_choice_posthoc_diagnostic(artifact)
    return artifact


def _validate_selection_preferences(value: object) -> None:
    routes = value.get("routes") if isinstance(value, Mapping) else None
    if not isinstance(routes, Mapping) or set(routes) != set(ROUTES):
        raise FairChoicePosthocError("selection-preference routes are incomplete")
    for route in routes.values():
        arms = route.get("arms") if isinstance(route, Mapping) else None
        ordinals = route.get("same_raw_action_pair_ordinals", [])
        if (
            not isinstance(arms, Mapping)
            or set(arms) != set(ARMS)
            or not _valid_ordinals(ordinals)
            or route.get("same_raw_action_pair_count") != len(ordinals)
            or not 0
            <= route.get("same_raw_action_pair_count", -1)
            <= route.get("both_arms_valid_pair_count", -1)
            <= benchmark.PAIR_COUNT
        ):
            raise FairChoicePosthocError("selection-preference pair counts drifted")
        for arm in ARMS:
            row = arms[arm]
            valid = row.get("valid_choice_count")
            invalid = row.get("invalid_choice_count")
            raw_counts = row.get("raw_action_index_counts")
            display_counts = row.get("display_position_counts")
            if (
                row.get("received_count") != benchmark.PAIR_COUNT
                or not _is_nonnegative_int(valid)
                or not _is_nonnegative_int(invalid)
                or valid + invalid != benchmark.PAIR_COUNT
                or not _valid_count_map(raw_counts)
                or set(raw_counts) != _RAW_INDICES
                or sum(raw_counts.values()) != valid
                or not _valid_count_map(display_counts)
                or set(display_counts) != _RAW_INDICES
                or sum(display_counts.values()) != valid
                or not _valid_count_map(row.get("path_counts"))
                or sum(row["path_counts"].values()) != valid
                or not _valid_count_map(row.get("frame_counts"))
                or sum(row["frame_counts"].values()) != valid
            ):
                raise FairChoicePosthocError("selection-preference counts drifted")


def _validate_baseline_overlaps(value: object) -> None:
    routes = value.get("routes") if isinstance(value, Mapping) else None
    if (
        not isinstance(routes, Mapping)
        or set(routes) != set(ROUTES)
        or value.get("selection_overlap_is_descriptive_not_shortcut_attribution")
        is not True
    ):
        raise FairChoicePosthocError("baseline-overlap routes are incomplete")
    overlap_cells = {
        "model_and_baseline",
        "model_only",
        "baseline_only",
        "neither",
    }
    for route in routes.values():
        policies = route.get("policies", [])
        if (
            route.get("policy_count") != len(benchmark.BASELINE_POLICY_IDS)
            or [row.get("policy_id") for row in policies]
            != list(benchmark.BASELINE_POLICY_IDS)
            or sum(bool(row.get("is_B_star")) for row in policies) != 1
        ):
            raise FairChoicePosthocError("frozen 24-policy family drifted")
        for policy in policies:
            for arm in ARMS:
                ordinals = policy.get(f"{arm}_action_match_pair_ordinals")
                if (
                    not _valid_ordinals(ordinals)
                    or policy.get(f"{arm}_action_match_count") != len(ordinals)
                ):
                    raise FairChoicePosthocError("baseline action matches drifted")
            both = policy.get("both_arms_action_match_pair_ordinals")
            if (
                not _valid_ordinals(both)
                or policy.get("both_arms_action_match_count") != len(both)
            ):
                raise FairChoicePosthocError("baseline pair matches drifted")
            overlap = policy.get("factual_K4_overlap")
            if not isinstance(overlap, Mapping) or set(overlap) != overlap_cells:
                raise FairChoicePosthocError("baseline K4 overlap is incomplete")
            covered: set[int] = set()
            for cell in overlap.values():
                ordinals = cell.get("pair_ordinals", [])
                if (
                    not _valid_ordinals(ordinals)
                    or cell.get("count") != len(ordinals)
                    or covered.intersection(ordinals)
                ):
                    raise FairChoicePosthocError("baseline K4 partition drifted")
                covered.update(ordinals)
            if covered != _PAIR_ORDINALS:
                raise FairChoicePosthocError("baseline K4 partition is incomplete")


def _validate_strong_hit_concentration(value: object) -> None:
    routes = value.get("routes") if isinstance(value, Mapping) else None
    if (
        value.get("arm") != "factual"
        or value.get("endpoint") != "K4_full_pool"
        or not isinstance(routes, Mapping)
        or set(routes) != set(ROUTES)
    ):
        raise FairChoicePosthocError("strong-hit concentration is incomplete")
    count_maps = (
        "child_behavior_hash_counts",
        "construction_stratum_counts",
        "raw_action_index_counts",
        "frame_counts",
        "counterfactual_bundle_sha256_counts",
    )
    for route in routes.values():
        hit_count = route.get("factual_K4_hit_count")
        rows = route.get("hit_rows")
        if (
            not _is_nonnegative_int(hit_count)
            or not isinstance(rows, list)
            or len(rows) != hit_count
            or route.get("constant_child_hit_count", -1)
            + route.get("nonconstant_child_hit_count", -1)
            != hit_count
            or any(
                not _valid_count_map(route.get(name))
                or sum(route[name].values()) != hit_count
                for name in count_maps
            )
            or route.get("unique_child_behavior_count")
            != len(route["child_behavior_hash_counts"])
        ):
            raise FairChoicePosthocError("strong-hit concentration counts drifted")


def _validate_opportunity_landscape(value: object) -> None:
    arms = value.get("benchmark_arms") if isinstance(value, Mapping) else None
    histogram_keys = {
        str(index) for index in range(benchmark.RAW_ACTION_COUNT + 1)
    }
    if not isinstance(arms, Mapping) or set(arms) != set(ARMS):
        raise FairChoicePosthocError("opportunity benchmark arms are incomplete")
    for arm in ARMS:
        if not isinstance(arms[arm], Mapping) or set(arms[arm]) != set(ENDPOINTS):
            raise FairChoicePosthocError("opportunity endpoints are incomplete")
        for endpoint in ENDPOINTS:
            row = arms[arm][endpoint]
            counts = row.get("per_pair_qualifying_action_counts")
            histogram = row.get("per_world_opportunity_count_histogram")
            raw_counts = row.get("raw_action_index_qualifying_counts")
            strata = row.get("by_construction_stratum")
            if (
                not isinstance(counts, list)
                or len(counts) != benchmark.PAIR_COUNT
                or any(
                    type(count) is not int
                    or not 0 <= count <= benchmark.RAW_ACTION_COUNT
                    for count in counts
                )
                or not _valid_count_map(histogram)
                or set(histogram) != histogram_keys
                or histogram
                != {str(count): counts.count(count) for count in range(11)}
                or row.get("qualifying_raw_action_count") != sum(counts)
                or row.get("world_with_opportunity_count")
                != sum(count > 0 for count in counts)
                or not _valid_count_map(raw_counts)
                or set(raw_counts) != _RAW_INDICES
                or sum(raw_counts.values()) != sum(counts)
                or not isinstance(strata, Mapping)
                or set(strata) != set(STRATA)
            ):
                raise FairChoicePosthocError("opportunity endpoint counts drifted")
            stratum_histogram = Counter()
            for stratum in STRATA:
                stratum_row = strata[stratum]
                stratum_hist = stratum_row.get(
                    "per_world_opportunity_count_histogram"
                )
                if (
                    stratum_row.get("pair_count") != 8
                    or not _valid_count_map(stratum_hist)
                    or set(stratum_hist) != histogram_keys
                    or sum(stratum_hist.values()) != 8
                    or stratum_row.get("qualifying_raw_action_count")
                    != sum(
                        int(count) * frequency
                        for count, frequency in stratum_hist.items()
                    )
                    or stratum_row.get("world_with_opportunity_count")
                    != 8 - stratum_hist["0"]
                ):
                    raise FairChoicePosthocError(
                        "construction-stratum opportunity counts drifted"
                    )
                stratum_histogram.update(stratum_hist)
            if (
                dict(stratum_histogram) != histogram
                or sum(row["by_construction_stratum"][name]["pair_count"] for name in STRATA)
                != benchmark.PAIR_COUNT
                or sum(
                    row["by_construction_stratum"][name][
                        "qualifying_raw_action_count"
                    ]
                    for name in STRATA
                )
                != row["qualifying_raw_action_count"]
                or sum(
                    row["by_construction_stratum"][name][
                        "world_with_opportunity_count"
                    ]
                    for name in STRATA
                )
                != row["world_with_opportunity_count"]
            ):
                raise FairChoicePosthocError(
                    "construction strata do not aggregate to the global opportunity"
                )

    tables = value.get("same_raw_frame_factual_sham_tables", {})
    if set(tables) != set(ENDPOINTS) or any(
        table.get("frame_count")
        != sum(
            table.get(cell, -benchmark.PAIR_COUNT)
            for cell in ("both", "factual_only", "sham_only", "neither")
        )
        or table.get("frame_count")
        != benchmark.PAIR_COUNT * benchmark.RAW_ACTION_COUNT
        for table in tables.values()
    ):
        raise FairChoicePosthocError("same-frame opportunity partition drifted")
    overlay = value.get("route_selected_opportunity_overlay", {})
    if set(overlay) != set(ROUTES):
        raise FairChoicePosthocError("selected-opportunity routes are incomplete")
    for route_id in ROUTES:
        if set(overlay[route_id]) != set(ARMS):
            raise FairChoicePosthocError("selected-opportunity arms are incomplete")
        for arm in ARMS:
            if set(overlay[route_id][arm]) != set(ENDPOINTS) or any(
                sum(categories.values()) != benchmark.PAIR_COUNT
                for categories in overlay[route_id][arm].values()
            ):
                raise FairChoicePosthocError(
                    "selected-opportunity partition drifted"
                )


def _validate_paired_selected_endpoint_decomposition(value: object) -> None:
    routes = value.get("routes") if isinstance(value, Mapping) else None
    if (
        value.get("interpretation") != PAIRED_SELECTION_INTERPRETATION
        or not isinstance(routes, Mapping)
        or set(routes) != set(ROUTES)
    ):
        raise FairChoicePosthocError("paired endpoint decomposition is incomplete")
    cell_names = {"both_hit", "factual_only", "sham_only", "neither"}
    for route in routes.values():
        if not isinstance(route, Mapping) or set(route) != set(ENDPOINTS):
            raise FairChoicePosthocError("paired endpoint routes are incomplete")
        for endpoint in route.values():
            if not isinstance(endpoint, Mapping) or set(endpoint) != {
                *cell_names,
                "invalid",
            }:
                raise FairChoicePosthocError("paired endpoint cells are incomplete")
            covered: set[int] = set()
            for name in cell_names:
                cell = endpoint[name]
                split_ordinals: set[int] = set()
                for relation in ("same_raw_action", "different_raw_action"):
                    payload = cell.get(relation, {})
                    ordinals = payload.get("pair_ordinals", [])
                    if (
                        not _valid_ordinals(ordinals)
                        or payload.get("count") != len(ordinals)
                        or split_ordinals.intersection(ordinals)
                    ):
                        raise FairChoicePosthocError(
                            "paired endpoint raw-action split drifted"
                        )
                    split_ordinals.update(ordinals)
                if cell.get("count") != len(split_ordinals) or covered.intersection(
                    split_ordinals
                ):
                    raise FairChoicePosthocError("paired endpoint cell drifted")
                covered.update(split_ordinals)
            invalid = endpoint["invalid"]
            invalid_ordinals = invalid.get("pair_ordinals", [])
            if (
                not _valid_ordinals(invalid_ordinals)
                or invalid.get("count") != len(invalid_ordinals)
                or covered.intersection(invalid_ordinals)
            ):
                raise FairChoicePosthocError("paired endpoint invalid cell drifted")
            covered.update(invalid_ordinals)
            if covered != _PAIR_ORDINALS:
                raise FairChoicePosthocError(
                    "paired endpoint cells do not partition all pairs"
                )


def validate_fair_choice_posthoc_diagnostic(artifact: Mapping[str, Any]) -> None:
    """Validate the closed descriptive artifact schema and self-digest."""

    expected_fields = {
        "schema_version",
        "kind",
        "protocol_id",
        "status",
        "post_hoc_explanatory_only",
        "evidence",
        "designed_after_formal_outcomes_opened",
        "provider_calls_made",
        "formal_analysis_mutated",
        "formal_analysis_recomputed",
        "new_p_values_computed",
        "new_classification_labels_created",
        "interpretation_limit",
        "historical_formal_source_manifest_sha256",
        "diagnostic_source_manifest_sha256",
        "input_bindings",
        "formal_result_reference",
        "selection_preferences",
        "baseline_overlaps",
        "strong_hit_concentration",
        "opportunity_landscape",
        "paired_selected_endpoint_decomposition",
        "posthoc_diagnostic_sha256",
    }
    if not isinstance(artifact, Mapping) or set(artifact) != expected_fields:
        raise FairChoicePosthocError("post-hoc diagnostic uses an unknown schema")
    unsigned = {
        key: value
        for key, value in artifact.items()
        if key != "posthoc_diagnostic_sha256"
    }
    if (
        artifact.get("schema_version") != SCHEMA_VERSION
        or artifact.get("kind") != POSTHOC_KIND
        or artifact.get("protocol_id") != benchmark.PROTOCOL_ID
        or artifact.get("status") != POSTHOC_STATUS
        or artifact.get("post_hoc_explanatory_only") is not True
        or artifact.get("evidence") is not False
        or artifact.get("designed_after_formal_outcomes_opened") is not True
        or artifact.get("provider_calls_made") != 0
        or artifact.get("formal_analysis_mutated") is not False
        or artifact.get("formal_analysis_recomputed") is not False
        or artifact.get("new_p_values_computed") is not False
        or artifact.get("new_classification_labels_created") is not False
        or artifact.get("interpretation_limit") != INTERPRETATION_LIMIT
        or not _is_sha256(
            artifact.get("historical_formal_source_manifest_sha256")
        )
        or not _is_sha256(artifact.get("diagnostic_source_manifest_sha256"))
        or artifact.get("posthoc_diagnostic_sha256") != _sha256_json(unsigned)
    ):
        raise FairChoicePosthocError("post-hoc identity or boundary flags drifted")
    bindings = artifact.get("input_bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != {
        "formal_plan",
        "public_manifest",
        "generation_bundle",
        "private_key",
        "formal_analysis",
    }:
        raise FairChoicePosthocError("post-hoc input bindings are malformed")
    for binding in bindings.values():
        if (
            not isinstance(binding, Mapping)
            or set(binding) != {"file_sha256", "inner_sha256"}
            or not _is_sha256(binding["file_sha256"])
            or not _is_sha256(binding["inner_sha256"])
        ):
            raise FairChoicePosthocError("post-hoc exact input binding is malformed")

    formal_reference = artifact.get("formal_result_reference")
    if (
        not isinstance(formal_reference, Mapping)
        or set(formal_reference)
        != {
            "formal_analysis_file_sha256",
            "formal_joint_analysis_sha256",
            "joint_classification",
            "route_classifications",
            "classification_recomputed",
            "classification_modified",
        }
        or formal_reference.get("joint_classification")
        not in _FORMAL_JOINT_LABELS
        or formal_reference.get("classification_recomputed") is not False
        or formal_reference.get("classification_modified") is not False
        or not _is_sha256(formal_reference.get("formal_analysis_file_sha256"))
        or not _is_sha256(formal_reference.get("formal_joint_analysis_sha256"))
        or set(formal_reference.get("route_classifications", {})) != set(ROUTES)
        or any(
            label not in _FORMAL_ROUTE_LABELS
            for label in formal_reference["route_classifications"].values()
        )
    ):
        raise FairChoicePosthocError("formal classification reference is malformed")
    if (
        bindings["formal_analysis"]["file_sha256"]
        != formal_reference["formal_analysis_file_sha256"]
        or bindings["formal_analysis"]["inner_sha256"]
        != formal_reference["formal_joint_analysis_sha256"]
    ):
        raise FairChoicePosthocError("formal-analysis binding/reference drifted")

    preferences = artifact.get("selection_preferences")
    baselines = artifact.get("baseline_overlaps")
    concentration = artifact.get("strong_hit_concentration")
    opportunities = artifact.get("opportunity_landscape")
    paired_decomposition = artifact.get(
        "paired_selected_endpoint_decomposition"
    )
    if (
        not isinstance(preferences, Mapping)
        or set(preferences.get("routes", {})) != set(ROUTES)
        or not isinstance(baselines, Mapping)
        or set(baselines.get("routes", {})) != set(ROUTES)
        or baselines.get(
            "selection_overlap_is_descriptive_not_shortcut_attribution"
        )
        is not True
        or not isinstance(concentration, Mapping)
        or concentration.get("arm") != "factual"
        or concentration.get("endpoint") != "K4_full_pool"
        or set(concentration.get("routes", {})) != set(ROUTES)
        or not isinstance(opportunities, Mapping)
        or set(opportunities.get("benchmark_arms", {})) != set(ARMS)
        or set(opportunities.get("same_raw_frame_factual_sham_tables", {}))
        != set(ENDPOINTS)
        or set(opportunities.get("route_selected_opportunity_overlay", {}))
        != set(ROUTES)
        or not isinstance(paired_decomposition, Mapping)
        or set(paired_decomposition.get("routes", {})) != set(ROUTES)
    ):
        raise FairChoicePosthocError("post-hoc diagnostic sections are malformed")
    for route_id in ROUTES:
        route_baselines = baselines["routes"][route_id]
        if (
            route_baselines.get("policy_count")
            != len(benchmark.BASELINE_POLICY_IDS)
            or [row.get("policy_id") for row in route_baselines.get("policies", [])]
            != list(benchmark.BASELINE_POLICY_IDS)
        ):
            raise FairChoicePosthocError("post-hoc baseline family drifted")
    _validate_selection_preferences(preferences)
    _validate_baseline_overlaps(baselines)
    _validate_strong_hit_concentration(concentration)
    _validate_opportunity_landscape(opportunities)
    _validate_paired_selected_endpoint_decomposition(paired_decomposition)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline fair-choice post-hoc explanatory diagnostics"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    diagnose = subparsers.add_parser("diagnose")
    diagnose.add_argument("--plan", type=Path, required=True)
    diagnose.add_argument("--expected-plan-file-sha256", required=True)
    diagnose.add_argument("--public", type=Path, required=True)
    diagnose.add_argument("--expected-public-file-sha256", required=True)
    diagnose.add_argument("--bundle", type=Path, required=True)
    diagnose.add_argument("--expected-bundle-file-sha256", required=True)
    diagnose.add_argument("--private", type=Path, required=True)
    diagnose.add_argument("--expected-private-file-sha256", required=True)
    diagnose.add_argument("--formal-analysis", type=Path, required=True)
    diagnose.add_argument("--expected-formal-analysis-file-sha256", required=True)
    diagnose.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command != "diagnose":
        raise AssertionError("unreachable post-hoc command")
    if os.path.lexists(args.output):
        raise FairChoicePosthocError(
            f"refusing to overwrite post-hoc output {args.output}"
        )
    artifact = build_fair_choice_posthoc_diagnostic(
        plan_path=args.plan,
        expected_plan_file_sha256=args.expected_plan_file_sha256,
        public_manifest_path=args.public,
        expected_public_manifest_file_sha256=(
            args.expected_public_file_sha256
        ),
        generation_bundle_path=args.bundle,
        expected_generation_bundle_file_sha256=(
            args.expected_bundle_file_sha256
        ),
        private_key_path=args.private,
        expected_private_key_file_sha256=args.expected_private_file_sha256,
        formal_analysis_path=args.formal_analysis,
        expected_formal_analysis_file_sha256=(
            args.expected_formal_analysis_file_sha256
        ),
    )
    try:
        file_sha256 = formal.write_fair_choice_formal_json_exclusive(
            artifact, args.output
        )
    except formal.FairChoiceFormalError as exc:
        raise FairChoicePosthocError(str(exc)) from exc
    print(
        json.dumps(
            {"output": str(args.output), "file_sha256": file_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


__all__ = [
    "FairChoicePosthocError",
    "INTERPRETATION_LIMIT",
    "PAIRED_SELECTION_INTERPRETATION",
    "POSTHOC_KIND",
    "POSTHOC_STATUS",
    "build_fair_choice_posthoc_diagnostic",
    "main",
    "validate_fair_choice_posthoc_diagnostic",
]


if __name__ == "__main__":
    raise SystemExit(main())
