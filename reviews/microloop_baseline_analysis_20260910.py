"""Descriptive code baselines for the frozen 12-world microloop draft.

This module consumes the fixed public/private construction artifact and runs
only deterministic public-reservoir policies.  It does not call a model and
does not use the private hypothesis bank for policy selection.  Private target
labels are used only after trajectory construction, for the pre-registered
offline score.

The default ``write_analysis`` entry point creates an exclusive, mode-restricted
artifact directory.  A partially written directory is intentionally not
resumed or overwritten; this keeps an interrupted analysis auditable.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable


HERE = Path(__file__).resolve()
ROOT = HERE.parents[1]
REVIEWS = HERE.parent
if str(REVIEWS) not in sys.path:
    sys.path.insert(0, str(REVIEWS))

import microloop_baselines_20260910 as baselines  # noqa: E402
import microloop_protocol_20260910 as protocol  # noqa: E402
import new_evidence_scoring_20260910 as scoring  # noqa: E402
from src import dsl  # noqa: E402


DRAFT = ROOT / "artifacts/microloop-draft-20260910"
OUT = ROOT / "artifacts/microloop-baselines-20260910"
WORLD_COUNT = 12
POLICIES = ("random", "balanced", "repeat")
STAGE_COUNT = 3
CANDIDATES = (
    "nonconstant_minnode",
    "minnode_allow_constants",
    "parent",
    "constant_0",
    "constant_1",
)
SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
CONSTANT_EXPRESSIONS = {"constant_0": "(const 0)", "constant_1": "(const 1)"}


def sha(path: str | Path) -> str:
    """Return the SHA-256 digest of a file's bytes."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} is not a SHA-256 digest")
    return value


def _points(rows: Sequence[Mapping[str, Any]]) -> list[tuple[int, int, int]]:
    return [tuple(row["point"]) for row in rows]


def _require_binary(value: Any, name: str) -> int:
    if type(value) is not int or value not in (0, 1):
        raise ValueError(f"{name} is not a binary integer")
    return value


def _validate_private_world(public: Mapping[str, Any], private: Mapping[str, Any]) -> None:
    """Validate the frozen pair before using target labels for scoring."""

    expected_private = {
        "ordinal", "world_seed", "target_seed", "world_hash", "bank",
        "evidence", "test", "target_behavior",
    }
    if set(private) != expected_private:
        raise ValueError("private world fields changed")
    if private["ordinal"] != public["ordinal"]:
        raise ValueError("public/private ordinal mismatch")
    if not isinstance(private["world_seed"], int) or not isinstance(private["target_seed"], int):
        raise ValueError("private seed type changed")
    _require_sha(private["world_hash"], "world_hash")

    evidence = private["evidence"]
    test = private["test"]
    target = private["target_behavior"]
    bank = private["bank"]
    if not isinstance(evidence, list) or len(evidence) != 49:
        raise ValueError("private evidence denominator changed")
    if not isinstance(test, list) or len(test) != 64:
        raise ValueError("private test denominator changed")
    if not isinstance(target, list) or len(target) != len(dsl.DOMAIN):
        raise ValueError("target domain denominator changed")
    for index, label in enumerate(target):
        _require_binary(label, f"target_behavior[{index}]")
    if _points(evidence) != [tuple(point) for point in public["query_points"]]:
        raise ValueError("public/private evidence points differ")
    if set(_points(private["test"])) & set(_points(public["D0"])):
        raise ValueError("D0/test overlap")
    if set(_points(private["test"])) & {tuple(point) for point in public["query_points"]}:
        raise ValueError("query/test overlap")
    if len(set(_points(test))) != 64:
        raise ValueError("test points are not distinct")
    if len(set(_points(evidence))) != 49:
        raise ValueError("evidence points are not distinct")

    target_by_point = dict(zip(dsl.DOMAIN, target, strict=True))
    for group_name, rows in (("D0", public["D0"]), ("evidence", evidence), ("test", test)):
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or set(row) != {"point", "label"}:
                raise ValueError(f"{group_name}[{index}] schema changed")
            point = tuple(row["point"])
            label = _require_binary(row["label"], f"{group_name}[{index}].label")
            if point not in target_by_point or target_by_point[point] != label:
                raise ValueError(f"{group_name} label does not match target behavior")

    if not isinstance(bank, list) or len(bank) != 256:
        raise ValueError("hypothesis bank denominator changed")
    vectors: set[tuple[int, ...]] = set()
    for index, expression in enumerate(bank):
        if not isinstance(expression, str):
            raise ValueError(f"bank[{index}] is not text")
        try:
            ast = dsl.parse_sexpr(expression)
            dsl.validate_expr(ast)
            vector = dsl.behavior_vector(ast)
        except (ValueError, TypeError, RecursionError) as exc:
            raise ValueError(f"bank[{index}] is not a valid DSL rule") from exc
        if set(vector) - {0, 1} or vector in vectors:
            raise ValueError("bank behavior semantics changed")
        vectors.add(vector)
        if any(dsl.evaluate(ast, row["point"]) != row["label"] for row in public["D0"]):
            raise ValueError("bank rule no longer fits D0")
    if tuple(target) not in vectors:
        raise ValueError("target behavior is not represented in bank")


def load_inputs(
    draft: str | Path = DRAFT,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load and validate the immutable aggregate construction artifact."""

    root = Path(draft)
    public_path, private_path, audit_path = (root / name for name in ("public.json", "private.json", "audit.json"))
    public, private, audit = (read_json(path) for path in (public_path, private_path, audit_path))
    if not isinstance(public, Mapping) or set(public) != {"worlds"}:
        raise ValueError("public aggregate schema changed")
    if not isinstance(private, Mapping) or set(private) != {"worlds"}:
        raise ValueError("private aggregate schema changed")
    if not isinstance(audit, Mapping):
        raise ValueError("audit aggregate schema changed")
    if audit.get("worlds") != WORLD_COUNT or audit.get("provider_calls") != 0:
        raise ValueError("input world/provider denominator changed")
    if audit.get("no_performance_filter") is not True:
        raise ValueError("input was performance filtered")
    if audit.get("splits_verified") is not True or audit.get("bank_semantics_verified") is not True:
        raise ValueError("input construction audit is incomplete")
    if _require_sha(audit.get("public_sha256"), "audit.public_sha256") != sha(public_path):
        raise ValueError("public artifact hash mismatch")
    if _require_sha(audit.get("private_sha256"), "audit.private_sha256") != sha(private_path):
        raise ValueError("private artifact hash mismatch")
    public_worlds, private_worlds = public["worlds"], private["worlds"]
    if not isinstance(public_worlds, list) or not isinstance(private_worlds, list):
        raise ValueError("world aggregates are not lists")
    if len(public_worlds) != WORLD_COUNT or len(private_worlds) != WORLD_COUNT:
        raise ValueError("world count changed")

    public_by_ordinal: dict[int, dict[str, Any]] = {}
    private_by_ordinal: dict[int, dict[str, Any]] = {}
    for item in public_worlds:
        if not isinstance(item, dict) or set(item) != {"ordinal", "task_id", "D0", "parent", "query_points"}:
            raise ValueError("public world schema changed")
        ordinal = item.get("ordinal")
        if type(ordinal) is not int or ordinal in public_by_ordinal:
            raise ValueError("public ordinal is duplicate or invalid")
        protocol.validate_public(item)
        if item["task_id"] != f"microloop-{ordinal}":
            raise ValueError("task identity changed")
        public_by_ordinal[ordinal] = item
    for item in private_worlds:
        if not isinstance(item, dict):
            raise ValueError("private world is not an object")
        ordinal = item.get("ordinal")
        if type(ordinal) is not int or ordinal in private_by_ordinal:
            raise ValueError("private ordinal is duplicate or invalid")
        private_by_ordinal[ordinal] = item
    if set(public_by_ordinal) != set(range(WORLD_COUNT)) or set(private_by_ordinal) != set(range(WORLD_COUNT)):
        raise ValueError("world ordinals are not exactly 0..11")
    for ordinal in range(WORLD_COUNT):
        _validate_private_world(public_by_ordinal[ordinal], private_by_ordinal[ordinal])
    return public_by_ordinal, private_by_ordinal, dict(audit)


def _score_expression(
    expression: str,
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    binding = {
        "D0": public["D0"],
        "visible_observations": observations,
        "test": private["test"],
        "target_behavior": private["target_behavior"],
    }
    result = scoring.score_expression(json.dumps({"expression": expression}), binding)
    # Retain the selected program in the per-world audit, while keeping all
    # score fields from the frozen scorer intact.
    return {"expression": expression, **result}


def _score_trajectory(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    trajectory: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if len(trajectory) != STAGE_COUNT:
        raise ValueError("baseline stage count changed")
    result: list[dict[str, Any]] = []
    for stage_index, stage in enumerate(trajectory):
        observations = stage.get("observations")
        if not isinstance(observations, list):
            raise ValueError("baseline observations are absent")
        selected = {
            "nonconstant_minnode": stage["nonconstant_minnode"],
            "minnode_allow_constants": stage["minnode_allow_constants"],
            "parent": stage["parent"],
            **CONSTANT_EXPRESSIONS,
        }
        scores = {
            name: _score_expression(expression, public, private, observations)
            for name, expression in selected.items()
        }
        result.append({
            "stage": stage_index,
            "observed_count": len(observations),
            "new_observations": max(0, len(observations) - len(public["D0"])),
            "scores": scores,
        })
    return result


def analyze_world(
    public: Mapping[str, Any],
    private: Mapping[str, Any],
    reservoir: Sequence[Any],
) -> dict[str, Any]:
    """Run all three deterministic public-policy trajectories for one world."""

    target_by_point = dict(zip(dsl.DOMAIN, private["target_behavior"], strict=True))

    def label_at(point: Sequence[int]) -> int:
        return _require_binary(target_by_point.get(tuple(point)), "oracle label")

    policy_results: dict[str, Any] = {}
    for policy in POLICIES:
        trajectory = baselines.simulate(public, reservoir, label_at, policy)
        policy_results[policy] = {
            "queries": [
                list(q) for q in (
                    baselines.schedule(public, False)
                    if policy == "random"
                    else baselines.schedule(public, True)
                    if policy == "repeat"
                    else [
                        trajectory[1]["observations"][-1]["point"],
                        trajectory[2]["observations"][-1]["point"],
                    ]
                )
            ],
            "stages": _score_trajectory(public, private, trajectory),
        }
    return {
        "ordinal": public["ordinal"],
        "task_id": public["task_id"],
        "world_hash": private["world_hash"],
        "policies": policy_results,
    }


def _mean(values: Sequence[float | int]) -> float:
    if not values:
        raise ValueError("cannot average an empty sequence")
    return sum(values) / len(values)


def _metric_summary(rows: Sequence[Mapping[str, Any]], metric: str) -> dict[str, Any]:
    values = [row[metric] for row in rows]
    if metric in {"test_perfect", "full_domain_exact", "valid_program", "D0_consistent", "visible_consistent"}:
        return {
            "worlds": len(values),
            "count": sum(bool(value) for value in values),
            "rate": _mean([int(bool(value)) for value in values]),
        }
    return {"worlds": len(values), "mean": _mean(values), "values": values}


def summarize(world_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Produce equal-world descriptive summaries and paired deltas."""

    if len(world_rows) != WORLD_COUNT or [row["ordinal"] for row in world_rows] != list(range(WORLD_COUNT)):
        raise ValueError("world summary denominator changed")
    conditions: dict[str, Any] = {}
    for policy in POLICIES:
        stage_summary: dict[str, Any] = {}
        for stage in range(STAGE_COUNT):
            candidate_summary: dict[str, Any] = {}
            for candidate in CANDIDATES:
                score_rows = [row["policies"][policy]["stages"][stage]["scores"][candidate] for row in world_rows]
                candidate_summary[candidate] = {
                    "worlds": WORLD_COUNT,
                    "mean_test_accuracy": _mean([r["test_accuracy"] for r in score_rows]),
                    "mean_test_correct": _mean([r["test_correct"] for r in score_rows]),
                    "test_perfect_worlds": sum(bool(r["test_perfect"]) for r in score_rows),
                    "full_domain_exact_worlds": sum(bool(r["full_domain_exact"]) for r in score_rows),
                    "valid_program_worlds": sum(bool(r["valid_program"]) for r in score_rows),
                    "D0_consistent_worlds": sum(bool(r["D0_consistent"]) for r in score_rows),
                    "visible_consistent_worlds": sum(bool(r["visible_consistent"]) for r in score_rows),
                    "mean_consistent_test_accuracy": _mean([r["consistent_test_accuracy"] for r in score_rows]),
                }
            stage_summary[str(stage)] = candidate_summary
        conditions[policy] = {
            "worlds": WORLD_COUNT,
            "stages": stage_summary,
            "mean_new_observations": _mean([
                row["policies"][policy]["stages"][-1]["new_observations"] for row in world_rows
            ]),
        }

    paired: dict[str, Any] = {}
    for left, right in (("balanced", "random"), ("balanced", "repeat"), ("random", "repeat")):
        comparison: dict[str, Any] = {}
        for stage in range(STAGE_COUNT):
            candidate_rows: dict[str, Any] = {}
            for candidate in CANDIDATES:
                left_scores = [row["policies"][left]["stages"][stage]["scores"][candidate] for row in world_rows]
                right_scores = [row["policies"][right]["stages"][stage]["scores"][candidate] for row in world_rows]
                deltas = [a["test_correct"] - b["test_correct"] for a, b in zip(left_scores, right_scores, strict=True)]
                candidate_rows[candidate] = {
                    "worlds": WORLD_COUNT,
                    "mean_test_correct_delta": _mean(deltas),
                    "mean_test_accuracy_delta": _mean(deltas) / 64,
                    "wins": sum(delta > 0 for delta in deltas),
                    "losses": sum(delta < 0 for delta in deltas),
                    "ties": sum(delta == 0 for delta in deltas),
                    "world_test_correct_deltas": deltas,
                }
            comparison[str(stage)] = candidate_rows
        paired[left + "_vs_" + right] = comparison
    return {
        "worlds": WORLD_COUNT,
        "policies": list(POLICIES),
        "candidates": list(CANDIDATES),
        "conditions": conditions,
        "paired_world_deltas": paired,
        "equal_world_weight": True,
        "new_inferential_tests": False,
    }


def _write_exclusive_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor != -1:
            os.close(descriptor)


def write_analysis(
    draft: str | Path = DRAFT,
    out: str | Path = OUT,
) -> dict[str, Any]:
    """Run and persist the full deterministic baseline analysis once."""

    public, private, input_audit = load_inputs(draft)
    target = Path(out)
    target.mkdir(mode=0o700, exist_ok=False)
    reservoir = baselines.public_reservoir()
    if not reservoir:
        raise ValueError("public reservoir is empty")
    rows: list[dict[str, Any]] = []
    world_hashes: dict[str, str] = {}
    for ordinal in range(WORLD_COUNT):
        # Persist each completed world before moving to the next one. An
        # interruption therefore leaves auditable per-world work, while the
        # aggregate summary/audit are emitted only after all 12 rows exist.
        row = analyze_world(public[ordinal], private[ordinal], reservoir)
        rows.append(row)
        path = target / f"world-{row['ordinal']:02d}.json"
        _write_exclusive_json(path, row)
        world_hashes[path.name] = sha(path)
        print("analyzed", ordinal + 1, "/12", flush=True)
    summary = summarize(rows)
    summary_payload = {
        "kind": "microloop_public_reservoir_baseline_summary",
        "input": {
            "public_sha256": sha(Path(draft) / "public.json"),
            "private_sha256": sha(Path(draft) / "private.json"),
            "audit_sha256": sha(Path(draft) / "audit.json"),
        },
        **summary,
    }
    summary_path = target / "summary.json"
    _write_exclusive_json(summary_path, summary_payload)
    audit_payload = {
        "kind": "microloop_public_reservoir_baseline_audit",
        "analysis_version": "20260910-v1",
        "worlds": WORLD_COUNT,
        "policies": list(POLICIES),
        "candidates": list(CANDIDATES),
        "provider_calls": 0,
        "selection_uses_private_bank": False,
        "selection_uses_unqueried_target_labels": False,
        "selection_uses_observed_labels": True,
        "performance_filter": False,
        "equal_world_weight": True,
        "new_inferential_tests": False,
        "input_hashes": summary_payload["input"],
        "input_construction_audit": {
            "no_performance_filter": input_audit["no_performance_filter"],
            "splits_verified": input_audit["splits_verified"],
            "bank_semantics_verified": input_audit["bank_semantics_verified"],
        },
        "reservoir_size": len(reservoir),
        "world_file_hashes": world_hashes,
        "summary_sha256": sha(summary_path),
        "scope": "offline public-reservoir code policies; descriptive comparator only, no model result",
    }
    audit_path = target / "audit.json"
    _write_exclusive_json(audit_path, audit_payload)
    return {"summary": summary_payload, "audit": audit_payload, "output": str(target)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, default=DRAFT)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    result = write_analysis(args.draft, args.out)
    print(json.dumps({
        "output": result["output"],
        "worlds": result["audit"]["worlds"],
        "policies": result["audit"]["policies"],
        "provider_calls": result["audit"]["provider_calls"],
        "summary_sha256": result["audit"]["summary_sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
