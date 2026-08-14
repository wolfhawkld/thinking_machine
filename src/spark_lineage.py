"""Structured, replayable motif edits for the spark experiment.

This module is deliberately research-sized.  It builds a frozen arithmetic
motif library, applies a small closed set of edits to a target-blind parent,
and retains only children whose lineage can be replayed exactly.  Eligibility
uses syntax, public ``D0`` and candidate behavior; it never consults the hidden
target or private-test labels.
"""

from __future__ import annotations

import functools
import hashlib
import json
from dataclasses import dataclass, replace
from typing import Literal, Sequence

from . import dsl
from .spark_world import SparkWorld, _small_arithmetic_expressions


MOTIF_STRATA = (
    "affine_commutative",
    "affine_directional",
    "affine_multiplicative",
    "pairwise_variable",
)
EDIT_PATHS = ((1, 1), (1, 2))
WRAP_OPERATORS = ("add", "sub", "mul")


class LineageError(ValueError):
    """Raised when an edit cannot be uniquely replayed or is ineligible."""


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _motif_stratum(ast: dsl.Expr) -> str | None:
    op = ast[0]
    if op in WRAP_OPERATORS and len(ast) == 3:
        operand_ops = {ast[1][0], ast[2][0]}
        if operand_ops == {"var"}:
            return "pairwise_variable"
        if operand_ops == {"var", "const"}:
            if op == "mul":
                return "affine_multiplicative"
            if op == "sub":
                return "affine_directional"
            return "affine_commutative"
    return None


@dataclass(frozen=True)
class ArithmeticMotif:
    motif_id: str
    stratum: str
    ast: dsl.Expr
    canonical_hash: str
    depth: int
    node_count: int

    @property
    def complexity_bucket(self) -> tuple[int, int]:
        return self.depth, self.node_count


@functools.lru_cache(maxsize=1)
def build_motif_library() -> tuple[ArithmeticMotif, ...]:
    """Return the frozen target-independent motif library."""

    motifs: list[ArithmeticMotif] = []
    for raw in _small_arithmetic_expressions():
        ast = dsl.canonicalize(raw)
        stratum = _motif_stratum(ast)  # type: ignore[arg-type]
        if stratum is None:
            continue
        digest = dsl.canonical_hash(ast)
        motifs.append(
            ArithmeticMotif(
                motif_id=f"{stratum}:{digest[:16]}",
                stratum=stratum,
                ast=ast,  # type: ignore[arg-type]
                canonical_hash=digest,
                depth=dsl.depth(ast),
                node_count=dsl.node_count(ast),
            )
        )
    result = tuple(sorted(motifs, key=lambda item: item.motif_id))
    if {item.stratum for item in result} != set(MOTIF_STRATA):
        raise RuntimeError("motif library does not cover all frozen strata")
    return result


def motif_by_id(motif_id: str) -> ArithmeticMotif:
    for motif in build_motif_library():
        if motif.motif_id == motif_id:
            return motif
    raise LineageError(f"unknown motif_id: {motif_id!r}")


@dataclass(frozen=True)
class EditAction:
    operation: Literal["replace", "wrap_binary"]
    path: tuple[int, ...]
    expected_old_subtree_hash: str
    motif_id: str
    binary_operator: Literal["add", "sub", "mul"] | None = None
    motif_side: Literal["left", "right"] | None = None

    def __post_init__(self) -> None:
        if self.path not in EDIT_PATHS:
            raise ValueError("edit path must be one of the frozen predicate operands")
        if self.operation == "replace":
            if self.binary_operator is not None or self.motif_side is not None:
                raise ValueError("replace edits cannot carry wrap fields")
        elif self.operation == "wrap_binary":
            if self.binary_operator not in WRAP_OPERATORS:
                raise ValueError("wrap_binary requires add, sub, or mul")
            if self.motif_side not in ("left", "right"):
                raise ValueError("wrap_binary requires a motif side")
        else:
            raise ValueError(f"unknown edit operation: {self.operation!r}")

    @property
    def action_hash(self) -> str:
        return _sha256_json(
            {
                "operation": self.operation,
                "path": self.path,
                "expected_old_subtree_hash": self.expected_old_subtree_hash,
                "motif_id": self.motif_id,
                "binary_operator": self.binary_operator,
                "motif_side": self.motif_side,
            }
        )

    def frame_key(self, motif: ArithmeticMotif) -> tuple[object, ...]:
        """Action identity with the particular motif removed."""

        return (
            self.operation,
            self.path,
            self.expected_old_subtree_hash,
            self.binary_operator,
            self.motif_side,
            motif.stratum,
            motif.complexity_bucket,
        )


@dataclass(frozen=True)
class MatchedReplacement:
    motif_id: str
    motif_stratum: str
    child_ast: dsl.Expr
    child_canonical_hash: str
    child_behavior_hash: str


@dataclass(frozen=True)
class LineageRecord:
    parent_ast: dsl.Expr
    parent_canonical_hash: str
    motif_id: str
    motif_stratum: str
    motif_ast: dsl.Expr
    motif_complexity_bucket: tuple[int, int]
    action: EditAction
    action_hash: str
    child_ast: dsl.Expr
    child_canonical_hash: str
    child_behavior_hash: str
    matched_replacements: tuple[MatchedReplacement, ...] = ()

    @property
    def matched_replacement_motif_ids(self) -> tuple[str, ...]:
        return tuple(item.motif_id for item in self.matched_replacements)

    @property
    def lineage_hash(self) -> str:
        return _sha256_json(
            {
                "parent": self.parent_canonical_hash,
                "motif": self.motif_id,
                "action": self.action_hash,
                "child": self.child_canonical_hash,
            }
        )


def _walk(ast: dsl.AST, path: tuple[int, ...] = ()):
    yield path, ast
    for index, child in enumerate(ast[1:], start=1):
        if isinstance(child, tuple):
            yield from _walk(child, path + (index,))


def get_subtree(ast: dsl.AST, path: Sequence[int]) -> dsl.AST:
    current: dsl.AST = ast
    for index in path:
        if type(index) is not int or index <= 0 or index >= len(current):
            raise LineageError(f"invalid AST path: {tuple(path)!r}")
        child = current[index]
        if not isinstance(child, tuple):
            raise LineageError(f"AST path does not identify a subtree: {tuple(path)!r}")
        current = child
    return current


def _replace_subtree(ast: dsl.AST, path: tuple[int, ...], new: dsl.AST) -> dsl.AST:
    if not path:
        return new
    index = path[0]
    if index <= 0 or index >= len(ast) or not isinstance(ast[index], tuple):
        raise LineageError(f"invalid AST path: {path!r}")
    values = list(ast)
    values[index] = _replace_subtree(ast[index], path[1:], new)
    return tuple(values)


def _occurrence_count(ast: dsl.AST, motif: dsl.AST) -> int:
    return sum(node == motif for _, node in _walk(ast))


def apply_edit(
    parent: dsl.Expr,
    motif: ArithmeticMotif,
    action: EditAction,
) -> dsl.Expr:
    """Apply one edit and return the canonical child."""

    parent_ast = dsl.canonicalize(parent)
    motif_ast = dsl.canonicalize(motif.ast)
    if action.motif_id != motif.motif_id:
        raise LineageError("action motif_id does not match the supplied motif")
    old = get_subtree(parent_ast, action.path)
    if dsl.canonical_hash(old) != action.expected_old_subtree_hash:
        raise LineageError("old subtree hash does not match the frozen action")
    # Lineage attribution is strict: the motif must be absent from the parent
    # and occur exactly once after the edit.  This makes the assigned context
    # the unique source of that subtree in the child.
    if _occurrence_count(parent_ast, motif_ast):
        raise LineageError("parent already contains the assigned motif")
    if action.operation == "replace":
        inserted: dsl.AST = motif_ast
    elif action.motif_side == "left":
        inserted = (action.binary_operator, motif_ast, old)
    else:
        inserted = (action.binary_operator, old, motif_ast)
    child = dsl.canonicalize(_replace_subtree(parent_ast, action.path, inserted))
    if _occurrence_count(child, motif_ast) != 1:
        raise LineageError("the child must contain the assigned motif exactly once")
    return child  # type: ignore[return-value]


replay_edit = apply_edit


def select_parent(world: SparkWorld) -> dsl.Expr:
    """Select the target-independent shortest/canonical bank member."""

    if not world.hypotheses:
        raise LineageError("world has no hypotheses")
    return min(
        world.hypotheses,
        key=lambda ast: (dsl.node_count(ast), dsl.canonical_hash(ast)),
    )


def _candidate_action_variants(
    parent: dsl.Expr,
    motif: ArithmeticMotif,
):
    for path in EDIT_PATHS:
        old = get_subtree(parent, path)
        old_hash = dsl.canonical_hash(old)
        yield EditAction("replace", path, old_hash, motif.motif_id)
        for operator in WRAP_OPERATORS:
            yield EditAction(
                "wrap_binary",
                path,
                old_hash,
                motif.motif_id,
                operator,
                "right",
            )
            if operator == "sub":
                yield EditAction(
                    "wrap_binary",
                    path,
                    old_hash,
                    motif.motif_id,
                    operator,
                    "left",
                )


def validate_lineage(
    world: SparkWorld,
    parent: dsl.Expr,
    motif: ArithmeticMotif,
    action: EditAction,
    *,
    expected_child: dsl.Expr | None = None,
) -> LineageRecord:
    """Replay and validate a lineage without consulting hidden outcomes."""

    parent_ast = dsl.canonicalize(parent)
    child = apply_edit(parent_ast, motif, action)
    if expected_child is not None and child != dsl.canonicalize(expected_child):
        raise LineageError("replayed child differs from the recorded child")
    try:
        dsl.validate_expr(child)
    except ValueError as exc:
        raise LineageError(f"child violates DSL constraints: {exc}") from exc
    child_behavior = dsl.behavior_vector(child, world.domain)
    if set(child_behavior) - {0, 1}:
        raise LineageError("child must be binary on the complete domain")
    parent_behavior = dsl.behavior_vector(parent_ast, world.domain)
    if child_behavior == parent_behavior:
        raise LineageError("child is not behaviorally novel on the complete domain")
    train_points = tuple(example.point for example in world.train)
    public_labels = tuple(example.label for example in world.train)
    if tuple(dsl.evaluate(child, point) for point in train_points) != public_labels:
        raise LineageError("child is inconsistent with public D0")
    evidence_points = tuple(example.point for example in world.evidence)
    if tuple(dsl.evaluate(child, point) for point in evidence_points) == tuple(
        dsl.evaluate(parent_ast, point) for point in evidence_points
    ):
        raise LineageError("child does not alter behavior on the evidence pool")
    return LineageRecord(
        parent_ast=parent_ast,  # type: ignore[arg-type]
        parent_canonical_hash=dsl.canonical_hash(parent_ast),
        motif_id=motif.motif_id,
        motif_stratum=motif.stratum,
        motif_ast=motif.ast,
        motif_complexity_bucket=motif.complexity_bucket,
        action=action,
        action_hash=action.action_hash,
        child_ast=child,
        child_canonical_hash=dsl.canonical_hash(child),
        child_behavior_hash=dsl.behavior_hash(child, world.domain),
    )


def enumerate_reachable_children(world: SparkWorld) -> tuple[LineageRecord, ...]:
    """Enumerate control-ready lineages without outcome-based filtering.

    Every structurally control-ready action frame is retained.  Semantic
    de-duplication happens only after the operational response-profile gate:
    two syntactic frames can reach the same child behavior while differing in
    whether same-frame replacements remain usable.  A retained record carries
    all deterministic same-frame replacement children that independently pass
    structural validation.  The operational calibrator applies its frozen
    response-profile gate and takes the first two by this fixed order; it never
    chooses controls by four-query performance.
    """

    parent = select_parent(world)
    raw: list[tuple[LineageRecord, ArithmeticMotif]] = []
    for motif in build_motif_library():
        for action in _candidate_action_variants(parent, motif):
            try:
                record = validate_lineage(world, parent, motif, action)
            except LineageError:
                continue
            raw.append((record, motif))

    by_frame: dict[tuple[object, ...], list[tuple[LineageRecord, ArithmeticMotif]]] = {}
    for record, motif in raw:
        by_frame.setdefault(record.action.frame_key(motif), []).append((record, motif))

    control_ready: list[LineageRecord] = []
    for record, motif in raw:
        replacements: list[MatchedReplacement] = []
        for other, other_motif in sorted(
            by_frame[record.action.frame_key(motif)],
            key=lambda pair: (pair[1].canonical_hash, pair[0].child_canonical_hash),
        ):
            if other_motif.motif_id == motif.motif_id:
                continue
            if other.child_behavior_hash == record.child_behavior_hash:
                continue
            replacements.append(
                MatchedReplacement(
                    motif_id=other_motif.motif_id,
                    motif_stratum=other_motif.stratum,
                    child_ast=other.child_ast,
                    child_canonical_hash=other.child_canonical_hash,
                    child_behavior_hash=other.child_behavior_hash,
                )
            )
        if len(replacements) >= 2:
            control_ready.append(
                replace(record, matched_replacements=tuple(replacements))
            )

    return tuple(
        sorted(
            control_ready,
            key=lambda item: (
                item.motif_stratum,
                item.child_behavior_hash,
                item.lineage_hash,
            ),
        )
    )


__all__ = [
    "EDIT_PATHS",
    "MOTIF_STRATA",
    "WRAP_OPERATORS",
    "ArithmeticMotif",
    "EditAction",
    "LineageError",
    "LineageRecord",
    "MatchedReplacement",
    "apply_edit",
    "build_motif_library",
    "enumerate_reachable_children",
    "get_subtree",
    "motif_by_id",
    "replay_edit",
    "select_parent",
    "validate_lineage",
]
