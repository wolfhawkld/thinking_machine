"""Exact, zero-API version-space compression for spark experiments.

The compressor operates on a finite, closed hypothesis bank.  Its oracle is an
equivalence-query oracle over a fixed, ordered evidence pool: a response is
either ``MATCH`` or the first mismatch together with the target label.  Version
updates compare the *complete response* for every hypothesis, so the matching
prefix before a counterexample is not accidentally discarded.

All hypothesis counts are semantic counts.  Programs with identical behavior
on the world's complete domain are represented by a single, deterministically
chosen AST.  This module computes private/full-domain outcomes immediately, so
``run`` is for offline calibration or evaluation after a trajectory has been
sealed.  Its query/update loop itself reads only the ordered evidence pool; a
live factorial runner must enforce the private-evaluation barrier around it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Final, Literal

from . import dsl
from .verifier import parse_candidate


MATCH: Final = "MATCH"
FIRST_MISMATCH: Final = "FIRST_MISMATCH"
MAX_COMPRESSION_ROUNDS: Final = 4
MAX_CALIBRATION_ROUNDS: Final = 6


class CompressionError(ValueError):
    """Base class for malformed worlds and inconsistent compression states."""


class EmptyVersionSpaceError(CompressionError):
    """Raised when an oracle response is incompatible with every hypothesis."""


@dataclass(frozen=True)
class OracleResponse:
    """One complete equivalence-oracle response.

    For a mismatch, ``index`` is its zero-based position in the frozen evidence
    order.  Including both index and target label makes response equality encode
    all preceding matches as well as the released counterexample.
    """

    kind: Literal["MATCH", "FIRST_MISMATCH"]
    index: int | None = None
    point: tuple[int, int, int] | None = None
    label: int | None = None

    def __post_init__(self) -> None:
        if self.kind == MATCH:
            if self.index is not None or self.point is not None or self.label is not None:
                raise ValueError("MATCH cannot carry mismatch fields")
            return
        if self.kind != FIRST_MISMATCH:
            raise ValueError(f"unknown oracle response kind: {self.kind!r}")
        if self.index is None or self.index < 0 or self.point is None or self.label is None:
            raise ValueError("FIRST_MISMATCH requires index, point, and label")

    @classmethod
    def match(cls) -> OracleResponse:
        return cls(MATCH)

    @classmethod
    def first_mismatch(
        cls,
        index: int,
        point: tuple[int, int, int],
        label: int,
    ) -> OracleResponse:
        return cls(FIRST_MISMATCH, index=index, point=point, label=label)

    @property
    def is_match(self) -> bool:
        return self.kind == MATCH

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if not self.is_match:
            payload.update(index=self.index, point=self.point, label=self.label)
        return payload


@dataclass(frozen=True)
class ExactLogRatio:
    """An exact integer ratio together with its derived base-two logarithm."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.numerator <= 0 or self.denominator <= 0:
            raise ValueError("log-ratio counts must both be positive")

    @property
    def ratio(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    @property
    def reduced_numerator(self) -> int:
        return self.ratio.numerator

    @property
    def reduced_denominator(self) -> int:
        return self.ratio.denominator

    @property
    def expression(self) -> str:
        return f"log2({self.numerator}/{self.denominator})"

    @property
    def bits(self) -> float:
        return math.log2(self.numerator / self.denominator)

    def to_dict(self) -> dict[str, int | float | str]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "reduced_numerator": self.reduced_numerator,
            "reduced_denominator": self.reduced_denominator,
            "expression": self.expression,
            "bits": self.bits,
        }


@dataclass(frozen=True)
class SemanticHypothesis:
    """One full-domain behavior class and its deterministic representative."""

    ast: dsl.Expr
    behavior: tuple[int, ...]
    node_count: int
    canonical_hash: str
    raw_indices: tuple[int, ...]

    @property
    def rank_key(self) -> tuple[int, str]:
        return self.node_count, self.canonical_hash


@dataclass(frozen=True)
class CertifiedFact:
    """A domain prediction on which the current version space agrees."""

    domain_index: int
    point: tuple[int, int, int]
    label: int


@dataclass(frozen=True)
class CompressionStep:
    """Exact accounting for one oracle query and version-space update."""

    round_index: int
    query_ast: dsl.Expr
    query_canonical_hash: str
    query_source: Literal["seed", "version_space"]
    response: OracleResponse
    version_size_before: int
    version_size_after: int
    eliminated_count: int
    log_ratio: ExactLogRatio
    truth_retained: bool
    consensus_count: int
    certified_fact_count: int
    newly_certified_fact_count: int

    @property
    def N_before(self) -> int:
        return self.version_size_before

    @property
    def N_after(self) -> int:
        return self.version_size_after

    @property
    def log_ratio_numerator(self) -> int:
        return self.log_ratio.numerator

    @property
    def log_ratio_denominator(self) -> int:
        return self.log_ratio.denominator

    @property
    def exact_log_ratio(self) -> str:
        return self.log_ratio.expression

    @property
    def log2_contraction(self) -> float:
        return self.log_ratio.bits

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_index": self.round_index,
            "query_ast": self.query_ast,
            "query_canonical_hash": self.query_canonical_hash,
            "query_source": self.query_source,
            "response": self.response.to_dict(),
            "N_before": self.N_before,
            "N_after": self.N_after,
            "eliminated_count": self.eliminated_count,
            "log_ratio": self.log_ratio.to_dict(),
            "truth_retained": self.truth_retained,
            "consensus_count": self.consensus_count,
            "certified_fact_count": self.certified_fact_count,
            "newly_certified_fact_count": self.newly_certified_fact_count,
        }


@dataclass(frozen=True)
class CompressionResult:
    """A complete, auditable compression trajectory."""

    seed_ast: dsl.Expr
    seed_canonical_hash: str
    initial_version_size: int
    final_version: tuple[SemanticHypothesis, ...]
    steps: tuple[CompressionStep, ...]
    selected_candidate: dsl.Expr
    selected_canonical_hash: str
    target_canonical_hash: str
    truth_retained: bool
    initial_consensus_count: int
    final_consensus_count: int
    certified_fact_count: int
    cumulative_log_ratio: ExactLogRatio
    full_domain_correct: int
    full_domain_total: int
    full_domain_recovered: bool
    test_correct: int
    test_total: int
    exact_identification: bool
    termination_reason: Literal["singleton", "evidence_equivalence", "round_limit"]

    @property
    def final_version_size(self) -> int:
        return len(self.final_version)

    @property
    def N_0(self) -> int:
        return self.initial_version_size

    @property
    def N_T(self) -> int:
        return self.final_version_size

    @property
    def test_accuracy(self) -> float:
        return self.test_correct / self.test_total if self.test_total else 0.0

    @property
    def full_domain_accuracy(self) -> float:
        return self.full_domain_correct / self.full_domain_total

    @property
    def rounds_completed(self) -> int:
        return len(self.steps)

    @property
    def version_sizes(self) -> tuple[int, ...]:
        return (self.initial_version_size, *(step.N_after for step in self.steps))

    @property
    def N_t(self) -> tuple[int, ...]:
        """Exact semantic version-space cardinality at every observed state."""

        return self.version_sizes

    @property
    def evidence_equivalent(self) -> bool:
        return self.termination_reason == "evidence_equivalence"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_ast": self.seed_ast,
            "seed_canonical_hash": self.seed_canonical_hash,
            "N_0": self.N_0,
            "N_T": self.N_T,
            "steps": tuple(step.to_dict() for step in self.steps),
            "selected_candidate": self.selected_candidate,
            "selected_canonical_hash": self.selected_canonical_hash,
            "target_canonical_hash": self.target_canonical_hash,
            "truth_retained": self.truth_retained,
            "initial_consensus_count": self.initial_consensus_count,
            "final_consensus_count": self.final_consensus_count,
            "certified_fact_count": self.certified_fact_count,
            "cumulative_log_ratio": self.cumulative_log_ratio.to_dict(),
            "full_domain_correct": self.full_domain_correct,
            "full_domain_total": self.full_domain_total,
            "full_domain_accuracy": self.full_domain_accuracy,
            "full_domain_recovered": self.full_domain_recovered,
            "test_correct": self.test_correct,
            "test_total": self.test_total,
            "test_accuracy": self.test_accuracy,
            "exact_identification": self.exact_identification,
            "termination_reason": self.termination_reason,
        }


_MISSING = object()


def _world_value(world: Any, *names: str, default: Any = _MISSING) -> Any:
    for name in names:
        if isinstance(world, Mapping) and name in world:
            return world[name]
        if hasattr(world, name):
            return getattr(world, name)
    if default is not _MISSING:
        return default
    raise CompressionError(f"world must provide one of: {', '.join(names)}")


def _normalize_point(point: Any) -> tuple[int, int, int]:
    if isinstance(point, Mapping):
        try:
            values = tuple(point[name] for name in dsl.VARIABLES)
        except KeyError as exc:
            raise CompressionError("mapping points require x1, x2, and x3") from exc
    else:
        try:
            values = tuple(point)
        except TypeError as exc:
            raise CompressionError(f"invalid point: {point!r}") from exc
    if len(values) != 3 or any(type(value) is not int for value in values):
        raise CompressionError(f"points must be integer triples: {point!r}")
    return values  # type: ignore[return-value]


def _point_and_optional_label(item: Any) -> tuple[tuple[int, int, int], int | None]:
    if isinstance(item, Mapping):
        point = next(
            (item[key] for key in ("point", "inputs", "x", "features") if key in item),
            _MISSING,
        )
        label = next(
            (item[key] for key in ("label", "y", "output", "target") if key in item),
            _MISSING,
        )
        if point is not _MISSING:
            return _normalize_point(point), None if label is _MISSING else int(label)
    for point_attr in ("point", "inputs", "x", "features"):
        if hasattr(item, point_attr):
            point = getattr(item, point_attr)
            label: Any = _MISSING
            for label_attr in ("label", "y", "output", "target"):
                if hasattr(item, label_attr):
                    label = getattr(item, label_attr)
                    break
            return _normalize_point(point), None if label is _MISSING else int(label)
    if (
        isinstance(item, Sequence)
        and not isinstance(item, (str, bytes))
        and len(item) == 2
        and isinstance(item[0], (Mapping, Sequence))
        and not isinstance(item[0], (str, bytes))
    ):
        return _normalize_point(item[0]), int(item[1])
    return _normalize_point(item), None


class SparkCompressor:
    """Run exact version-space compression for one duck-typed spark world."""

    def __init__(
        self,
        world: Any,
        *,
        max_depth: int = dsl.DEFAULT_MAX_DEPTH,
        max_nodes: int = dsl.DEFAULT_MAX_NODES,
        output_bound: int | None = dsl.DEFAULT_OUTPUT_BOUND,
    ) -> None:
        self.world = world
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.output_bound = output_bound

        self.domain = tuple(
            _normalize_point(point) for point in _world_value(world, "domain")
        )
        if not self.domain or len(set(self.domain)) != len(self.domain):
            raise CompressionError("world.domain must contain unique points")

        raw_hypotheses = tuple(_world_value(world, "hypotheses"))
        if not raw_hypotheses:
            raise CompressionError("world.hypotheses cannot be empty")
        target_index = _world_value(world, "target_index")
        if type(target_index) is not int or not 0 <= target_index < len(raw_hypotheses):
            raise CompressionError("world.target_index is out of range")

        normalized = tuple(self._normalize_candidate(item) for item in raw_hypotheses)
        self.target_ast = normalized[target_index]
        self.target_behavior = dsl.behavior_vector(self.target_ast, self.domain)
        self.target_canonical_hash = dsl.canonical_hash(self.target_ast)
        self.hypotheses = self._semantic_hypotheses(normalized)

        train_items = tuple(
            _world_value(world, "train", "train_examples", "train_points")
        )
        train = tuple(_point_and_optional_label(item) for item in train_items)
        self.train_points = tuple(point for point, _ in train)
        if len(set(self.train_points)) != len(self.train_points):
            raise CompressionError("training points must be unique")
        if any(point not in set(self.domain) for point in self.train_points):
            raise CompressionError("every training point must belong to world.domain")
        self._validate_supplied_labels(train, split="train")
        self.train_labels = tuple(
            dsl.evaluate(self.target_ast, point) for point in self.train_points
        )
        for hypothesis in self.hypotheses:
            predictions = tuple(
                dsl.evaluate(hypothesis.ast, point) for point in self.train_points
            )
            if predictions != self.train_labels:
                raise CompressionError(
                    "every semantic hypothesis must agree with all initial "
                    "training observations (V0 must equal the frozen bank)"
                )

        evidence_items = tuple(
            _world_value(
                world,
                "evidence",
                "evidence_examples",
                "evidence_points",
                "evidence_pool",
                "ordered_evidence",
                "ordered_points",
            )
        )
        evidence = tuple(_point_and_optional_label(item) for item in evidence_items)
        self.evidence_points = tuple(point for point, _ in evidence)
        if len(set(self.evidence_points)) != len(self.evidence_points):
            raise CompressionError("ordered evidence points must be unique")
        if any(point not in set(self.domain) for point in self.evidence_points):
            raise CompressionError("every evidence point must belong to world.domain")
        self._validate_supplied_labels(evidence, split="evidence")

        test_items = tuple(_world_value(world, "test", "test_examples", default=()))
        test = tuple(_point_and_optional_label(item) for item in test_items)
        if any(point not in set(self.domain) for point, _ in test):
            raise CompressionError("every test point must belong to world.domain")
        self._validate_supplied_labels(test, split="test")
        self.test_points = tuple(point for point, _ in test)
        self.test_labels = tuple(dsl.evaluate(self.target_ast, point) for point in self.test_points)

        if not self.truth_retained(self.hypotheses):
            raise CompressionError("semantic hypothesis bank does not retain the target law")

    def _normalize_candidate(self, candidate: Any) -> dsl.Expr:
        try:
            ast = dsl.canonicalize(parse_candidate(candidate))
            dsl.validate_expr(
                ast,
                domain=self.domain,
                max_depth=self.max_depth,
                max_nodes=self.max_nodes,
                output_bound=self.output_bound,
            )
        except Exception as exc:
            raise CompressionError(f"invalid candidate: {exc}") from exc
        return ast  # type: ignore[return-value]

    def _semantic_hypotheses(
        self,
        hypotheses: tuple[dsl.Expr, ...],
    ) -> tuple[SemanticHypothesis, ...]:
        grouped: dict[tuple[int, ...], list[tuple[int, dsl.Expr]]] = {}
        for raw_index, ast in enumerate(hypotheses):
            behavior = dsl.behavior_vector(ast, self.domain)
            grouped.setdefault(behavior, []).append((raw_index, ast))

        records: list[SemanticHypothesis] = []
        for behavior, members in grouped.items():
            representative = min(
                (ast for _, ast in members),
                key=lambda ast: (dsl.node_count(ast), dsl.canonical_hash(ast)),
            )
            records.append(
                SemanticHypothesis(
                    ast=representative,
                    behavior=behavior,
                    node_count=dsl.node_count(representative),
                    canonical_hash=dsl.canonical_hash(representative),
                    raw_indices=tuple(index for index, _ in members),
                )
            )
        return tuple(sorted(records, key=lambda record: record.rank_key))

    def _validate_supplied_labels(
        self,
        examples: tuple[tuple[tuple[int, int, int], int | None], ...],
        *,
        split: str,
    ) -> None:
        for point, supplied in examples:
            expected = dsl.evaluate(self.target_ast, point)
            if supplied is not None and supplied != expected:
                raise CompressionError(
                    f"{split} label {supplied} disagrees with target label {expected} at {point}"
                )

    def oracle_response(self, target: Any, candidate: Any) -> OracleResponse:
        """Return ``O(target, candidate)`` over the frozen evidence order."""

        target_ast = target.ast if isinstance(target, SemanticHypothesis) else target
        candidate_ast = candidate.ast if isinstance(candidate, SemanticHypothesis) else candidate
        target_ast = self._normalize_candidate(target_ast)
        candidate_ast = self._normalize_candidate(candidate_ast)
        for index, point in enumerate(self.evidence_points):
            label = dsl.evaluate(target_ast, point)
            if label != dsl.evaluate(candidate_ast, point):
                return OracleResponse.first_mismatch(index, point, label)
        return OracleResponse.match()

    def update_version_space(
        self,
        version_space: Sequence[SemanticHypothesis],
        candidate: Any,
        observed_response: OracleResponse,
    ) -> tuple[SemanticHypothesis, ...]:
        """Filter by complete oracle-response equivalence, not just one label."""

        candidate_ast = self._normalize_candidate(candidate)
        updated = tuple(
            hypothesis
            for hypothesis in version_space
            if self.oracle_response(hypothesis.ast, candidate_ast) == observed_response
        )
        if not updated:
            raise EmptyVersionSpaceError(
                "oracle response eliminated every semantic hypothesis; "
                "this is inconsistency, not infinite compression"
            )
        return updated

    @staticmethod
    def select_candidate(version_space: Sequence[SemanticHypothesis]) -> SemanticHypothesis:
        """Frozen downstream rule: node count, then canonical hash."""

        if not version_space:
            raise EmptyVersionSpaceError("cannot select from an empty version space")
        return min(version_space, key=lambda hypothesis: hypothesis.rank_key)

    def truth_retained(self, version_space: Sequence[SemanticHypothesis]) -> bool:
        return any(item.behavior == self.target_behavior for item in version_space)

    def consensus_facts(
        self,
        version_space: Sequence[SemanticHypothesis],
    ) -> tuple[CertifiedFact, ...]:
        if not version_space:
            raise EmptyVersionSpaceError("empty version spaces have no certified facts")
        facts: list[CertifiedFact] = []
        for index, point in enumerate(self.domain):
            values = {hypothesis.behavior[index] for hypothesis in version_space}
            if len(values) == 1:
                facts.append(CertifiedFact(index, point, next(iter(values))))
        return tuple(facts)

    def run(
        self,
        seed_candidate: Any,
        *,
        max_rounds: int = MAX_COMPRESSION_ROUNDS,
    ) -> CompressionResult:
        """Run deterministic compression beginning with any legal seed.

        Four rounds is the proposed live default.  Offline floor/ceiling
        calibration may explicitly request up to six rounds before that live
        budget is frozen.  Because the returned object includes test and
        full-domain outcomes, call this only during offline calibration or
        after every factual/control trajectory has been sealed.  Candidate
        selection and version updates do not read the test split.
        """

        if type(max_rounds) is not int or not 0 <= max_rounds <= MAX_CALIBRATION_ROUNDS:
            raise ValueError(f"max_rounds must be between 0 and {MAX_CALIBRATION_ROUNDS}")
        seed_ast = self._normalize_candidate(seed_candidate)
        version = self.hypotheses
        initial_size = len(version)
        initial_facts = self.consensus_facts(version)
        initial_fact_indices = {fact.domain_index for fact in initial_facts}
        previous_fact_indices = initial_fact_indices
        steps: list[CompressionStep] = []
        evidence_equivalent = False

        for round_index in range(max_rounds):
            if len(version) == 1:
                break
            if round_index == 0:
                query_ast = seed_ast
                query_source: Literal["seed", "version_space"] = "seed"
            else:
                query_ast = self.select_candidate(version).ast
                query_source = "version_space"

            response = self.oracle_response(self.target_ast, query_ast)
            before = len(version)
            updated = self.update_version_space(version, query_ast, response)
            after = len(updated)
            facts = self.consensus_facts(updated)
            fact_indices = {fact.domain_index for fact in facts}
            truth_retained = self.truth_retained(updated)
            steps.append(
                CompressionStep(
                    round_index=round_index,
                    query_ast=query_ast,
                    query_canonical_hash=dsl.canonical_hash(query_ast),
                    query_source=query_source,
                    response=response,
                    version_size_before=before,
                    version_size_after=after,
                    eliminated_count=before - after,
                    log_ratio=ExactLogRatio(before, after),
                    truth_retained=truth_retained,
                    consensus_count=len(facts),
                    certified_fact_count=len(fact_indices - initial_fact_indices),
                    newly_certified_fact_count=len(fact_indices - previous_fact_indices),
                )
            )
            if not truth_retained:
                raise CompressionError("version update removed the target semantic law")
            version = updated
            previous_fact_indices = fact_indices
            # MATCH means every retained hypothesis agrees with the query on
            # every evidence point.  If more than one semantic law remains,
            # this evidence pool cannot distinguish them; another selected
            # member would deterministically return MATCH and contract nothing.
            if response.is_match and len(version) > 1:
                evidence_equivalent = True
                break

        selected = self.select_candidate(version)
        final_facts = self.consensus_facts(version)
        final_fact_indices = {fact.domain_index for fact in final_facts}
        full_correct = sum(
            predicted == target
            for predicted, target in zip(selected.behavior, self.target_behavior, strict=True)
        )
        test_correct = sum(
            dsl.evaluate(selected.ast, point) == label
            for point, label in zip(self.test_points, self.test_labels, strict=True)
        )
        truth_retained = self.truth_retained(version)
        full_recovered = full_correct == len(self.domain)
        exact_identification = len(version) == 1 and truth_retained and full_recovered
        if len(version) == 1:
            termination_reason: Literal[
                "singleton", "evidence_equivalence", "round_limit"
            ] = "singleton"
        elif evidence_equivalent:
            termination_reason = "evidence_equivalence"
        else:
            termination_reason = "round_limit"
        return CompressionResult(
            seed_ast=seed_ast,
            seed_canonical_hash=dsl.canonical_hash(seed_ast),
            initial_version_size=initial_size,
            final_version=version,
            steps=tuple(steps),
            selected_candidate=selected.ast,
            selected_canonical_hash=selected.canonical_hash,
            target_canonical_hash=self.target_canonical_hash,
            truth_retained=truth_retained,
            initial_consensus_count=len(initial_facts),
            final_consensus_count=len(final_facts),
            certified_fact_count=len(final_fact_indices - initial_fact_indices),
            cumulative_log_ratio=ExactLogRatio(initial_size, len(version)),
            full_domain_correct=full_correct,
            full_domain_total=len(self.domain),
            full_domain_recovered=full_recovered,
            test_correct=test_correct,
            test_total=len(self.test_points),
            exact_identification=exact_identification,
            termination_reason=termination_reason,
        )


# Descriptive aliases keep callers independent of the class naming choice.
ExactSparkCompressor = SparkCompressor


def run_compression(
    world: Any,
    seed_candidate: Any,
    *,
    max_rounds: int = MAX_COMPRESSION_ROUNDS,
) -> CompressionResult:
    """Convenience wrapper for a one-off exact compression trajectory."""

    return SparkCompressor(world).run(seed_candidate, max_rounds=max_rounds)


compress = run_compression


__all__ = [
    "CertifiedFact",
    "CompressionError",
    "CompressionResult",
    "CompressionStep",
    "EmptyVersionSpaceError",
    "ExactLogRatio",
    "ExactSparkCompressor",
    "FIRST_MISMATCH",
    "MATCH",
    "MAX_CALIBRATION_ROUNDS",
    "MAX_COMPRESSION_ROUNDS",
    "OracleResponse",
    "SemanticHypothesis",
    "SparkCompressor",
    "compress",
    "run_compression",
]
