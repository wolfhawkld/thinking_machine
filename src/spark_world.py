"""Target-blind, finite binary-classifier worlds for spark experiments.

The global reservoir contains small DSL classifiers with distinct behavior on
the complete 125-point domain.  A world first freezes twelve training points,
then conditions the reservoir on the largest shared 12-bit response signature.
It selects 256 members without consulting a target or any calibration outcome.
The remaining points are split into 49 ordered evidence and 64 private test
points; the selected bank is required to be identifiable on either split.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import json
import random
from dataclasses import dataclass

from .dsl import (
    CONSTANTS,
    DOMAIN,
    Expr,
    Point,
    behavior_vector,
    canonical_hash,
    canonicalize,
    evaluate,
    node_count,
    to_sexpr,
    validate_expr,
)
from .world_generator import Example


SPARK_BANK_SIZE = 256
SPARK_INITIAL_HARTLEY_BITS = 8
SPARK_TRAIN_SIZE = 12
SPARK_EVIDENCE_SIZE = 49
SPARK_TEST_SIZE = 64


@dataclass(frozen=True)
class SparkWorld:
    world_seed: int
    target_seed: int
    hypotheses: tuple[Expr, ...]
    target_index: int
    train: tuple[Example, ...]
    evidence: tuple[Example, ...]
    test: tuple[Example, ...]
    world_hash: str
    reservoir_size: int
    conditioning_group_size: int
    domain: tuple[Point, ...] = DOMAIN

    @property
    def seed(self) -> int:
        return self.world_seed

    @property
    def target(self) -> Expr:
        return self.hypotheses[self.target_index]

    law = target
    target_hypothesis = target

    @property
    def hypothesis_bank(self) -> tuple[Expr, ...]:
        return self.hypotheses

    train_examples = property(lambda self: self.train)
    evidence_examples = property(lambda self: self.evidence)
    test_examples = property(lambda self: self.test)
    train_points = property(lambda self: self.train)
    evidence_points = property(lambda self: self.evidence)
    test_points = property(lambda self: self.test)
    x_train = property(lambda self: tuple(item.point for item in self.train))
    y_train = property(lambda self: tuple(item.label for item in self.train))
    x_evidence = property(lambda self: tuple(item.point for item in self.evidence))
    y_evidence = property(lambda self: tuple(item.label for item in self.evidence))
    x_test = property(lambda self: tuple(item.point for item in self.test))
    y_test = property(lambda self: tuple(item.label for item in self.test))
    X_train = x_train
    Y_train = y_train
    X_evidence = x_evidence
    Y_evidence = y_evidence
    X_test = x_test
    Y_test = y_test


def _namespaced_rng(seed: int, namespace: str) -> random.Random:
    encoded = f"conditioned-binary-spark-v1:{namespace}:{seed}".encode("ascii")
    return random.Random(int.from_bytes(hashlib.sha256(encoded).digest(), "big"))


def _small_arithmetic_expressions() -> tuple[Expr, ...]:
    variables = [("var", name) for name in ("x1", "x2", "x3")]
    constants = [("const", value) for value in CONSTANTS]
    raw: list[Expr] = variables + constants + [("neg", item) for item in variables]
    for operator in ("add", "sub", "mul"):
        raw.extend(
            (operator, left, right)
            for left in variables
            for right in variables + constants
        )
        raw.extend(
            (operator, left, right)
            for left in constants
            for right in variables
        )
    unique = {to_sexpr(item): canonicalize(item) for item in raw}
    return tuple(unique[key] for key in sorted(unique))  # type: ignore[return-value]


@functools.lru_cache(maxsize=1)
def build_classifier_reservoir() -> tuple[Expr, ...]:
    """Return the global full-domain-semantic reservoir of small classifiers."""

    expressions = _small_arithmetic_expressions()
    vectors = {to_sexpr(item): behavior_vector(item) for item in expressions}
    zero, one = ("const", 0), ("const", 1)
    representatives: dict[tuple[int, ...], Expr] = {}
    for operator in ("gt", "eq"):
        for left, right in itertools.product(expressions, repeat=2):
            left_values = vectors[to_sexpr(left)]
            right_values = vectors[to_sexpr(right)]
            behavior = tuple(
                int(a > b) if operator == "gt" else int(a == b)
                for a, b in zip(left_values, right_values, strict=True)
            )
            if len(set(behavior)) < 2:
                continue
            classifier = canonicalize(
                ("ite", (operator, left, right), one, zero)
            )
            validate_expr(classifier)
            incumbent = representatives.get(behavior)
            rank = (node_count(classifier), canonical_hash(classifier))
            if incumbent is None or rank < (
                node_count(incumbent),
                canonical_hash(incumbent),
            ):
                representatives[behavior] = classifier  # type: ignore[assignment]
    return tuple(
        sorted(
            representatives.values(),
            key=lambda item: (node_count(item), canonical_hash(item)),
        )
    )


def _projection(ast: Expr, points: tuple[Point, ...]) -> tuple[int, ...]:
    return tuple(evaluate(ast, point) for point in points)


@functools.lru_cache(maxsize=None)
def _world_structure(
    world_seed: int,
) -> tuple[tuple[Expr, ...], tuple[Point, ...], tuple[Point, ...], tuple[Point, ...], int]:
    reservoir = build_classifier_reservoir()

    train_rng = _namespaced_rng(world_seed, "train")
    axis = train_rng.randrange(3)
    value = train_rng.choice((-2, -1, 0, 1, 2))
    face = [point for point in DOMAIN if point[axis] == value]
    train_rng.shuffle(face)
    train_points = tuple(face[:SPARK_TRAIN_SIZE])

    signature_groups: dict[tuple[int, ...], list[Expr]] = {}
    for hypothesis in reservoir:
        signature_groups.setdefault(_projection(hypothesis, train_points), []).append(
            hypothesis
        )
    signature, conditioned = min(
        signature_groups.items(), key=lambda item: (-len(item[1]), item[0])
    )
    del signature
    if len(conditioned) < SPARK_BANK_SIZE:
        raise RuntimeError("largest training-signature group has fewer than 256 members")

    train_set = set(train_points)
    remaining = [point for point in DOMAIN if point not in train_set]
    split_rng = _namespaced_rng(world_seed, "evidence-test")
    split_rng.shuffle(remaining)
    evidence_points = tuple(remaining[:SPARK_EVIDENCE_SIZE])
    test_points = tuple(remaining[SPARK_EVIDENCE_SIZE:])

    candidate_order = list(conditioned)
    _namespaced_rng(world_seed, "bank").shuffle(candidate_order)
    bank: list[Expr] = []
    evidence_behaviors: set[tuple[int, ...]] = set()
    test_behaviors: set[tuple[int, ...]] = set()
    for hypothesis in candidate_order:
        evidence_behavior = _projection(hypothesis, evidence_points)
        test_behavior = _projection(hypothesis, test_points)
        if evidence_behavior in evidence_behaviors or test_behavior in test_behaviors:
            continue
        bank.append(hypothesis)
        evidence_behaviors.add(evidence_behavior)
        test_behaviors.add(test_behavior)
        if len(bank) == SPARK_BANK_SIZE:
            break
    if len(bank) != SPARK_BANK_SIZE:
        raise RuntimeError("conditioned reservoir cannot supply a distinguishable bank")
    return (
        tuple(bank),
        train_points,
        evidence_points,
        test_points,
        len(conditioned),
    )


def build_hypothesis_bank(world_seed: int = 0) -> tuple[Expr, ...]:
    """Return the target-blind 256-member bank selected by ``world_seed``."""

    if type(world_seed) is not int:
        raise TypeError("world_seed must be an integer")
    return _world_structure(world_seed)[0]


def _label(points: tuple[Point, ...], target: Expr) -> tuple[Example, ...]:
    return tuple(Example(point, evaluate(target, point)) for point in points)


def _world_hash(
    world_seed: int,
    target_seed: int,
    bank: tuple[Expr, ...],
    target_index: int,
    train: tuple[Example, ...],
    evidence: tuple[Example, ...],
    test: tuple[Example, ...],
) -> str:
    payload = {
        "world_seed": world_seed,
        "target_seed": target_seed,
        "hypotheses": [to_sexpr(item) for item in bank],
        "target_index": target_index,
        "train": [(list(item.point), item.label) for item in train],
        "evidence": [(list(item.point), item.label) for item in evidence],
        "test": [(list(item.point), item.label) for item in test],
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def generate_spark_world(world_seed: int, target_seed: int) -> SparkWorld:
    """Construct one world; calibration outcomes never enter this function."""

    if type(world_seed) is not int or type(target_seed) is not int:
        raise TypeError("world_seed and target_seed must be integers")
    bank, train_points, evidence_points, test_points, group_size = _world_structure(
        world_seed
    )
    target_index = random.Random(target_seed).randrange(SPARK_BANK_SIZE)
    target = bank[target_index]
    train = _label(train_points, target)
    evidence = _label(evidence_points, target)
    test = _label(test_points, target)
    return SparkWorld(
        world_seed=world_seed,
        target_seed=target_seed,
        hypotheses=bank,
        target_index=target_index,
        train=train,
        evidence=evidence,
        test=test,
        world_hash=_world_hash(
            world_seed, target_seed, bank, target_index, train, evidence, test
        ),
        reservoir_size=len(build_classifier_reservoir()),
        conditioning_group_size=group_size,
    )


def build_spark_world(world_seed: int, target_seed: int) -> SparkWorld:
    return generate_spark_world(world_seed, target_seed)


__all__ = [
    "SPARK_BANK_SIZE",
    "SPARK_EVIDENCE_SIZE",
    "SPARK_INITIAL_HARTLEY_BITS",
    "SPARK_TEST_SIZE",
    "SPARK_TRAIN_SIZE",
    "SparkWorld",
    "build_classifier_reservoir",
    "build_hypothesis_bank",
    "build_spark_world",
    "generate_spark_world",
]
