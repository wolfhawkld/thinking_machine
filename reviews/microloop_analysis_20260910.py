"""Descriptive aggregation for the offline microloop experiment.

This module deliberately has no provider, credential, or filesystem code.  The
``aggregate`` function replays the public state machine for each supplied model
response and uses private evidence only through the label callback for an
actually executed query.  It is therefore suitable for running after a live
response bundle has been frozen, without changing the protocol while scoring.
"""

from collections.abc import Mapping, Sequence
import copy

import microloop_protocol_20260910 as protocol


WORLD_ORDINALS = tuple(range(12))
RESPONSE_SLOTS = 3
CONDITIONS = protocol.CONDITIONS


def _world_list(value, name):
    """Return a shallow list from either a world list or ``{"worlds": ...}``.

    The artifact files use the wrapped form, while small callers and tests
    commonly pass the list directly.  Supporting both forms does not alter the
    scoring contract; all subsequent validation is strict.
    """
    if isinstance(value, Mapping):
        if set(value) != {"worlds"}:
            raise ValueError(f"{name} must contain only worlds")
        value = value["worlds"]
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be a sequence of worlds")
    return list(value)


def _validate_worlds(public_worlds, private_worlds):
    publics = _world_list(public_worlds, "public_worlds")
    privates = _world_list(private_worlds, "private_worlds")
    if len(publics) != len(WORLD_ORDINALS) or len(privates) != len(WORLD_ORDINALS):
        raise ValueError("Expected exactly 12 public and private worlds")

    expected = set(WORLD_ORDINALS)
    public_by_ordinal = {}
    private_by_ordinal = {}
    for world in publics:
        if not isinstance(world, Mapping):
            raise ValueError("Public world must be an object")
        ordinal = world.get("ordinal")
        if type(ordinal) is not int or ordinal not in expected or ordinal in public_by_ordinal:
            raise ValueError("Public world ordinals must be unique 0..11")
        # Let the protocol perform the complete public-schema validation.
        protocol.validate_public(world)
        public_by_ordinal[ordinal] = world

    for world in privates:
        if not isinstance(world, Mapping):
            raise ValueError("Private world must be an object")
        ordinal = world.get("ordinal")
        if type(ordinal) is not int or ordinal not in expected or ordinal in private_by_ordinal:
            raise ValueError("Private world ordinals must be unique 0..11")
        for field in ("evidence", "test", "target_behavior"):
            if field not in world:
                raise ValueError(f"Private world lacks {field}")
        private_by_ordinal[ordinal] = world

    if set(public_by_ordinal) != expected or set(private_by_ordinal) != expected:
        raise ValueError("World ordinals must be exactly 0..11")
    for ordinal in WORLD_ORDINALS:
        public = public_by_ordinal[ordinal]
        private = private_by_ordinal[ordinal]
        if not isinstance(private["evidence"], Sequence) or isinstance(private["evidence"], (str, bytes)):
            raise ValueError("Private evidence must be a sequence")
        if len(private["evidence"]) != len(public["query_points"]):
            raise ValueError("Private evidence/query-pool sizes differ")
        evidence_points = []
        for row in private["evidence"]:
            if not isinstance(row, Mapping) or set(row) != {"point", "label"}:
                raise ValueError("Private evidence row schema changed")
            point = tuple(row["point"])
            if point in evidence_points or point not in map(tuple, public["query_points"]):
                raise ValueError("Private evidence is not the public query pool")
            if type(row["label"]) is not int or row["label"] not in (0, 1):
                raise ValueError("Private evidence labels must be binary")
            evidence_points.append(point)
        if set(evidence_points) != {tuple(point) for point in public["query_points"]}:
            raise ValueError("Private evidence does not cover the query pool")
    return public_by_ordinal, private_by_ordinal


def _records_by_key(records):
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValueError("records must be a sequence")
    result = {}
    known_ordinals = set(WORLD_ORDINALS)
    for record in records:
        if not isinstance(record, Mapping) or set(record) != {"ordinal", "condition", "responses"}:
            raise ValueError("Each record must contain ordinal, condition, and responses")
        ordinal = record["ordinal"]
        condition = record["condition"]
        if type(ordinal) is not int or ordinal not in known_ordinals:
            raise ValueError("Unknown world ordinal in records")
        if condition not in CONDITIONS:
            raise ValueError("Unknown condition in records")
        responses = record["responses"]
        if isinstance(responses, (str, bytes)) or not isinstance(responses, Sequence) or len(responses) != RESPONSE_SLOTS:
            raise ValueError("Each record must contain exactly three responses")
        normalized = list(responses)
        if any(value is not None and not isinstance(value, str) for value in normalized):
            raise ValueError("Responses must be strings or None")
        key = (ordinal, condition)
        if key in result:
            raise ValueError("Duplicate ordinal/condition record")
        result[key] = normalized
    return result


def _label_callback(private):
    """Build a callback backed only by private labels for query points."""
    labels = {tuple(row["point"]): row["label"] for row in private["evidence"]}

    def label_at(point):
        try:
            return labels[tuple(point)]
        except KeyError as exc:
            # This should be impossible after public/private validation, but a
            # clear error is preferable to silently scoring a leaked label.
            raise ValueError("Executed query has no private evidence label") from exc

    return label_at


def _stage_metrics(rows):
    metrics = []
    for stage in range(3):
        scores = [row["score"]["stages"][stage] for row in rows]
        mean = sum(score["test_accuracy"] for score in scores) / len(scores)
        valid = sum(bool(score["valid_program"]) for score in scores)
        visible = sum(bool(score["visible_consistent"]) for score in scores)
        exact = sum(bool(score["full_domain_exact"]) for score in scores)
        metrics.append({
            "stage": stage,
            "denominator": len(scores),
            "mean_test_accuracy": mean,
            # ``mean`` and ``valid`` are compact aliases retained alongside
            # explicit names for readable downstream tables.
            "mean": mean,
            "valid": valid,
            "valid_program": valid,
            "visible_consistent": visible,
            "full_domain_exact": exact,
        })
    return metrics


def _trajectory(public, private, responses, condition):
    state = protocol.initial_state(public, condition)
    label_at = _label_callback(private)
    for content in responses:
        state = protocol.advance(public, state, content, label_at)
    score = protocol.score_trajectory(public, private, state)
    history = state["history"]
    query_valid = sum(bool(event["proposed_query_valid"]) for event in history[:2])
    query_executed = sum(event["executed_query"] is not None for event in history[:2])
    feedback_count = sum(event["feedback"] is not None for event in history[:2])
    missing = sum(content is None for content in responses)
    return {
        "ordinal": public["ordinal"],
        "condition": condition,
        "score": copy.deepcopy(score),
        "stages": copy.deepcopy(score["stages"]),
        "final_test_accuracy": score["final_test_accuracy"],
        "initial_to_final_accuracy_delta": score["initial_to_final_accuracy_delta"],
        "gain": score["initial_to_final_accuracy_delta"],
        "new_labels": score["new_observations"],
        "query_valid": query_valid,
        "valid_queries": query_valid,
        "query_executed": query_executed,
        "executed_queries": query_executed,
        "feedback_count": feedback_count,
        "missing_responses": missing,
    }


def _condition_summary(rows):
    denominator = len(rows)
    stage_metrics = _stage_metrics(rows)
    mean_gain = sum(row["gain"] for row in rows) / denominator
    return {
        "worlds": denominator,
        "denominator": denominator,
        "stage_metrics": stage_metrics,
        "mean_gain": mean_gain,
        "new_labels": sum(row["new_labels"] for row in rows),
        "mean_new_labels": sum(row["new_labels"] for row in rows) / denominator,
        "query_valid": sum(row["query_valid"] for row in rows),
        "valid_queries": sum(row["query_valid"] for row in rows),
        "query_executed": sum(row["query_executed"] for row in rows),
        "executed_queries": sum(row["query_executed"] for row in rows),
        "missing_responses": sum(row["missing_responses"] for row in rows),
        "world_scores": rows,
        # This name matches the project's earlier descriptive aggregators.
        "world_rows": rows,
    }


def _paired(left_rows, right_rows, left, right):
    left_rows = sorted(left_rows, key=lambda row: row["ordinal"])
    right_rows = sorted(right_rows, key=lambda row: row["ordinal"])
    if [row["ordinal"] for row in left_rows] != list(WORLD_ORDINALS) or [row["ordinal"] for row in right_rows] != list(WORLD_ORDINALS):
        raise ValueError("Paired world denominator changed")
    final_deltas = [
        a["final_test_accuracy"] - b["final_test_accuracy"]
        for a, b in zip(left_rows, right_rows, strict=True)
    ]
    gain_deltas = [
        a["gain"] - b["gain"]
        for a, b in zip(left_rows, right_rows, strict=True)
    ]

    def signs(values):
        return {
            "wins": sum(value > 0 for value in values),
            "losses": sum(value < 0 for value in values),
            "ties": sum(value == 0 for value in values),
        }

    final_signs = signs(final_deltas)
    gain_signs = signs(gain_deltas)
    return {
        "left": left,
        "right": right,
        "worlds": len(final_deltas),
        "mean_final_accuracy_delta": sum(final_deltas) / len(final_deltas),
        "final_accuracy_delta_mean": sum(final_deltas) / len(final_deltas),
        "final_accuracy_deltas": final_deltas,
        **final_signs,
        "mean_gain_delta": sum(gain_deltas) / len(gain_deltas),
        "gain_delta_mean": sum(gain_deltas) / len(gain_deltas),
        "gain_deltas": gain_deltas,
        "gain_wins": gain_signs["wins"],
        "gain_losses": gain_signs["losses"],
        "gain_ties": gain_signs["ties"],
    }


def aggregate(public_worlds, private_worlds, records):
    """Replay and descriptively aggregate 12 worlds × 3 conditions.

    Parameters are in-memory public worlds, private worlds, and response
    records.  A response record has exactly ``ordinal``, ``condition`` and a
    three-item ``responses`` list; each item is a JSON string or ``None``.
    Missing records mean three missing responses for that trajectory.  The
    return value always keeps a fixed denominator of 12 per condition and
    contains per-world stage scores and paired descriptive contrasts.  No
    inferential test or p-value is computed: the 36 trajectories and 108
    response slots are repeated measurements nested in 12 worlds, not 108
    independent samples.
    """
    public_by_ordinal, private_by_ordinal = _validate_worlds(public_worlds, private_worlds)
    response_map = _records_by_key(records)
    conditions = {}
    for condition in CONDITIONS:
        rows = []
        for ordinal in WORLD_ORDINALS:
            responses = response_map.get((ordinal, condition), [None] * RESPONSE_SLOTS)
            rows.append(_trajectory(public_by_ordinal[ordinal], private_by_ordinal[ordinal], responses, condition))
        conditions[condition] = _condition_summary(rows)

    contrasts = {}
    for left, right in (("active", "random"), ("active", "repeat"), ("random", "repeat")):
        key = f"{left}_vs_{right}"
        contrasts[key] = _paired(
            conditions[left]["world_scores"], conditions[right]["world_scores"], left, right
        )
    return {
        "conditions": conditions,
        "contrasts": contrasts,
        "design": {
            "worlds": 12,
            "conditions": 3,
            "trajectories": 36,
            "response_slots": 108,
            "independent_unit": "world",
            "responses_are_not_independent_samples": True,
            "inferential_tests": False,
            "p_values_computed": False,
            "note": "36 trajectories and 108 response slots are nested in 12 paired worlds; contrasts are descriptive only.",
        },
        "new_inferential_tests": False,
    }


__all__ = ["aggregate", "CONDITIONS", "WORLD_ORDINALS"]
