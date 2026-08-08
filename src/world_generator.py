"""Procedural, closed-world tasks for the entropy-scheduling experiment.

The generator creates a hidden integer-valued law, then splits a finite input
domain into train, probe and test points.  It deliberately contains no
external data or network access: every label is computed by the deterministic
interpreter in :mod:`src.dsl`.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from typing import Sequence

from .dsl import (
    CONSTANTS,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_NODES,
    DEFAULT_OUTPUT_BOUND,
    DOMAIN,
    Expr,
    Point,
    behavior_hash,
    canonicalize,
    evaluate,
    hidden_law_constraints,
    node_count,
    to_sexpr,
    depth as expr_depth,
)


DEFAULT_TRAIN_SIZE = 12
DEFAULT_PROBE_SIZE = 12
DEFAULT_TEST_SIZE = 64
DEFAULT_DEPTH_TIERS = (3, 4, 5)
MAX_LAW_ATTEMPTS = 10_000


@dataclass(frozen=True)
class Example:
    """One labeled intervention/observation in a synthetic world."""

    point: Point
    label: int

    @property
    def x(self) -> Point:
        """Alias used by callers that call an input tuple ``x``."""

        return self.point

    @property
    def y(self) -> int:
        """Alias used by callers that call an output ``y``."""

        return self.label


@dataclass(frozen=True)
class SyntheticWorld:
    """A generated world and its train/probe/test split.

    ``law`` is retained for the local verifier and tests.  Runner/prompt code
    must use only the public train split and must never expose ``law`` or the
    probe/test labels to the model.
    """

    seed: int
    law: Expr
    train: tuple[Example, ...]
    probe: tuple[Example, ...]
    test: tuple[Example, ...]
    depth_tier: int
    world_hash: str
    # Retained for callers that need to compute full-domain behavior vectors.
    # The default preserves compatibility with manually constructed worlds.
    domain: tuple[Point, ...] = DOMAIN

    @property
    def x_train(self) -> tuple[Point, ...]:
        return tuple(example.point for example in self.train)

    @property
    def y_train(self) -> tuple[int, ...]:
        return tuple(example.label for example in self.train)

    @property
    def x_probe(self) -> tuple[Point, ...]:
        return tuple(example.point for example in self.probe)

    @property
    def y_probe(self) -> tuple[int, ...]:
        return tuple(example.label for example in self.probe)

    @property
    def x_test(self) -> tuple[Point, ...]:
        return tuple(example.point for example in self.test)

    @property
    def y_test(self) -> tuple[int, ...]:
        return tuple(example.label for example in self.test)

    # Upper-case aliases mirror the notation in experiment-spec.md and make the
    # split boundary explicit for prompt/verifier adapters.
    @property
    def X_train(self) -> tuple[Point, ...]:
        return self.x_train

    @property
    def Y_train(self) -> tuple[int, ...]:
        return self.y_train

    @property
    def X_probe(self) -> tuple[Point, ...]:
        return self.x_probe

    @property
    def Y_probe(self) -> tuple[int, ...]:
        return self.y_probe

    @property
    def X_test(self) -> tuple[Point, ...]:
        return self.x_test

    @property
    def Y_test(self) -> tuple[int, ...]:
        return self.y_test

    @property
    def train_points(self) -> tuple[Example, ...]:
        return self.train

    @property
    def probe_points(self) -> tuple[Example, ...]:
        return self.probe

    @property
    def test_points(self) -> tuple[Example, ...]:
        return self.test

    @property
    def train_examples(self) -> tuple[Example, ...]:
        return self.train

    @property
    def probe_examples(self) -> tuple[Example, ...]:
        return self.probe

    @property
    def test_examples(self) -> tuple[Example, ...]:
        return self.test

    @property
    def law_sexpr(self) -> str:
        return to_sexpr(self.law)

    @property
    def law_behavior_hash(self) -> str:
        return behavior_hash(self.law, self.domain)


def _validate_domain(domain: Sequence[Point]) -> tuple[Point, ...]:
    points = tuple(tuple(point) for point in domain)  # type: ignore[arg-type]
    if len(set(points)) != len(points):
        raise ValueError("domain points must be unique")
    if not points:
        raise ValueError("domain cannot be empty")
    if any(len(point) != 3 or any(type(value) is not int for value in point) for point in points):
        raise ValueError("domain points must be integer triples")
    return points


def _leaf(rng: random.Random) -> Expr:
    if rng.random() < 0.62:
        return ("var", rng.choice(("x1", "x2", "x3")))
    return ("const", rng.choice(CONSTANTS))


def _bounded_expr(rng: random.Random, max_depth: int) -> Expr:
    """Generate an expression with depth at most ``max_depth``."""

    if max_depth <= 1 or rng.random() < 0.26:
        return _leaf(rng)

    # Multiplication and conditionals are deliberately less frequent because
    # they make bounded integer laws much rarer at depth five.
    choices = ["add", "sub", "mul", "neg"]
    weights = [30, 27, 16, 14]
    # A predicate has a minimum depth of two, so an ite cannot fit under an
    # expression depth budget of two.
    if max_depth >= 3:
        choices.append("ite")
        weights.append(5)
    choice = rng.choices(choices, weights=weights, k=1)[0]
    if choice == "neg":
        return ("neg", _bounded_expr(rng, max_depth - 1))
    if choice in {"add", "sub", "mul"}:
        return (
            choice,
            _bounded_expr(rng, max_depth - 1),
            _bounded_expr(rng, max_depth - 1),
        )
    # At max_depth >= 3, this predicate has depth at most max_depth - 1.
    predicate_depth = max(1, max_depth - 1)
    predicate = _bounded_predicate(rng, predicate_depth)
    return (
        "ite",
        predicate,
        _bounded_expr(rng, max_depth - 1),
        _bounded_expr(rng, max_depth - 1),
    )


def _bounded_predicate(rng: random.Random, max_depth: int) -> tuple:
    # A predicate has one operator node plus two expression children.  If no
    # room is available for a predicate at a requested depth, use leaf
    # children; its actual depth is then two, which is still safe for callers
    # that ask for max_depth >= 2.
    child_depth = max(1, max_depth - 1)
    return (
        rng.choice(("gt", "eq")),
        _bounded_expr(rng, child_depth),
        _bounded_expr(rng, child_depth),
    )


def _exact_expr(rng: random.Random, target_depth: int) -> Expr:
    """Generate an expression whose depth is exactly ``target_depth``."""

    if target_depth <= 1:
        return _leaf(rng)

    # An ite with a deep predicate is needlessly difficult to control, so the
    # guaranteed deep path is placed in one of its value branches.
    choices = ["add", "sub", "mul", "neg"]
    if target_depth >= 3:
        choices.append("ite")
    choice = rng.choices(choices, weights=[30, 27, 16, 14] + ([5] if "ite" in choices else []), k=1)[0]
    if choice == "neg":
        return ("neg", _exact_expr(rng, target_depth - 1))
    if choice in {"add", "sub", "mul"}:
        deep = _exact_expr(rng, target_depth - 1)
        other = _bounded_expr(rng, rng.randint(1, target_depth - 1))
        if rng.random() < 0.5:
            left, right = deep, other
        else:
            left, right = other, deep
        return (choice, left, right)

    predicate = _bounded_predicate(rng, max(2, target_depth - 1))
    deep = _exact_expr(rng, target_depth - 1)
    other = _bounded_expr(rng, rng.randint(1, target_depth - 1))
    if rng.random() < 0.5:
        then_branch, else_branch = deep, other
    else:
        then_branch, else_branch = other, deep
    return ("ite", predicate, then_branch, else_branch)


def _fallback_law(rng: random.Random, target_depth: int) -> Expr:
    """Return a small bounded law if random rejection sampling is exhausted."""

    x1 = ("var", "x1")
    x2 = ("var", "x2")
    x3 = ("var", "x3")
    c1 = ("const", rng.choice((-2, -1, 1, 2)))
    c2 = ("const", rng.choice((-2, -1, 1, 2)))
    if target_depth <= 3:
        return ("add", ("mul", x1, x2), ("sub", x3, c1))
    if target_depth == 4:
        return (
            "add",
            ("mul", ("add", x1, c1), x2),
            ("sub", x3, c2),
        )
    return (
        "sub",
        ("mul", ("add", ("mul", x1, x2), c1), x3),
        ("neg", ("add", x2, c2)),
    )


def _sample_law(
    rng: random.Random,
    target_depth: int,
    *,
    domain: Sequence[Point],
    max_depth: int,
    max_nodes: int,
    output_bound: int,
    max_attempts: int = MAX_LAW_ATTEMPTS,
) -> Expr:
    for _ in range(max_attempts):
        candidate = canonicalize(_exact_expr(rng, target_depth))
        if expr_depth(candidate) != target_depth:
            continue
        if node_count(candidate) > max_nodes:
            continue
        try:
            hidden_law_constraints(
                candidate,
                domain=domain,
                max_depth=max_depth,
                max_nodes=max_nodes,
                output_bound=output_bound,
            )
        except ValueError:
            continue
        return candidate  # type: ignore[return-value]

    fallback = canonicalize(_fallback_law(rng, target_depth))
    hidden_law_constraints(
        fallback,
        domain=domain,
        max_depth=max_depth,
        max_nodes=max_nodes,
        output_bound=output_bound,
    )
    return fallback


def _split_examples(
    law: Expr,
    *,
    seed: int,
    domain: Sequence[Point],
    train_size: int,
    probe_size: int,
    test_size: int,
) -> tuple[tuple[Example, ...], tuple[Example, ...], tuple[Example, ...]]:
    total = train_size + probe_size + test_size
    if min(train_size, probe_size, test_size) < 0:
        raise ValueError("split sizes cannot be negative")
    if total > len(domain):
        raise ValueError("train/probe/test split exceeds domain size")
    points = list(domain)
    # Keep law generation and point allocation independent while remaining
    # reproducible across Python processes.
    split_rng = random.Random(seed ^ 0x5EED_5EED_5EED_5EED)
    split_rng.shuffle(points)
    train_points = points[:train_size]
    probe_start = train_size
    probe_points = points[probe_start : probe_start + probe_size]
    test_start = probe_start + probe_size
    test_points = points[test_start : test_start + test_size]

    def label(point: Point) -> Example:
        return Example(point=point, label=evaluate(law, point))

    return (
        tuple(label(point) for point in train_points),
        tuple(label(point) for point in probe_points),
        tuple(label(point) for point in test_points),
    )


def _world_hash(
    *,
    seed: int,
    domain: Sequence[Point],
    law: Expr,
    train: Sequence[Example],
    probe: Sequence[Example],
    test: Sequence[Example],
    depth_tier: int,
) -> str:
    payload = {
        "seed": seed,
        "depth_tier": depth_tier,
        "domain": [list(point) for point in domain],
        "law": to_sexpr(law),
        "train": [(list(example.point), example.label) for example in train],
        "probe": [(list(example.point), example.label) for example in probe],
        "test": [(list(example.point), example.label) for example in test],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class WorldGenerator:
    """Generate deterministic synthetic worlds from integer seeds."""

    domain: tuple[Point, ...] = DOMAIN
    train_size: int = 12
    probe_size: int = 12
    test_size: int = 64
    max_depth: int = DEFAULT_MAX_DEPTH
    max_nodes: int = DEFAULT_MAX_NODES
    output_bound: int = DEFAULT_OUTPUT_BOUND
    depth_tiers: tuple[int, ...] = DEFAULT_DEPTH_TIERS
    max_law_attempts: int = MAX_LAW_ATTEMPTS

    def __post_init__(self) -> None:
        object.__setattr__(self, "domain", _validate_domain(self.domain))
        if not self.depth_tiers:
            raise ValueError("depth_tiers cannot be empty")
        if any(t < 1 or t > self.max_depth for t in self.depth_tiers):
            raise ValueError("depth tiers must lie within max_depth")
        if self.max_depth < 1 or self.max_nodes < 1 or self.output_bound < 0:
            raise ValueError("invalid DSL bounds")
        if min(self.train_size, self.probe_size, self.test_size) < 0:
            raise ValueError("split sizes cannot be negative")
        if self.train_size + self.probe_size + self.test_size > len(self.domain):
            raise ValueError("split sizes exceed domain size")

    def generate(self, seed: int, depth: int | None = None) -> SyntheticWorld:
        if type(seed) is not int:
            raise TypeError("world seed must be an integer")
        if depth is None:
            # This deterministic assignment gives approximately equal tiers for
            # consecutive integer seed lists and is easier to preregister than
            # relying on a random draw.
            depth_tier = self.depth_tiers[seed % len(self.depth_tiers)]
        else:
            if type(depth) is not int:
                raise TypeError("depth must be an integer")
            if depth not in self.depth_tiers:
                raise ValueError(f"depth must be one of {self.depth_tiers}")
            depth_tier = depth

        law_rng = random.Random(seed ^ 0x1A2B_3C4D_5E6F_7788)
        law = _sample_law(
            law_rng,
            depth_tier,
            domain=self.domain,
            max_depth=self.max_depth,
            max_nodes=self.max_nodes,
            output_bound=self.output_bound,
            max_attempts=self.max_law_attempts,
        )
        train, probe, test = _split_examples(
            law,
            seed=seed,
            domain=self.domain,
            train_size=self.train_size,
            probe_size=self.probe_size,
            test_size=self.test_size,
        )
        return SyntheticWorld(
            seed=seed,
            law=law,
            train=train,
            probe=probe,
            test=test,
            depth_tier=depth_tier,
            world_hash=_world_hash(
                seed=seed,
                domain=self.domain,
                law=law,
                train=train,
                probe=probe,
                test=test,
                depth_tier=depth_tier,
            ),
            domain=self.domain,
        )


def generate_world(
    seed: int,
    *,
    depth: int | None = None,
    domain: Sequence[Point] = DOMAIN,
    train_size: int = DEFAULT_TRAIN_SIZE,
    probe_size: int = DEFAULT_PROBE_SIZE,
    test_size: int = DEFAULT_TEST_SIZE,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    output_bound: int = DEFAULT_OUTPUT_BOUND,
    depth_tiers: Sequence[int] = DEFAULT_DEPTH_TIERS,
    max_law_attempts: int = MAX_LAW_ATTEMPTS,
) -> SyntheticWorld:
    """Convenience wrapper around :class:`WorldGenerator`."""

    generator = WorldGenerator(
        domain=tuple(domain),
        train_size=train_size,
        probe_size=probe_size,
        test_size=test_size,
        max_depth=max_depth,
        max_nodes=max_nodes,
        output_bound=output_bound,
        depth_tiers=tuple(depth_tiers),
        max_law_attempts=max_law_attempts,
    )
    return generator.generate(seed, depth=depth)


__all__ = [
    "DEFAULT_DEPTH_TIERS",
    "DEFAULT_PROBE_SIZE",
    "DEFAULT_TEST_SIZE",
    "DEFAULT_TRAIN_SIZE",
    "Example",
    "SyntheticWorld",
    "WorldGenerator",
    "generate_world",
]
