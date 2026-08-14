"""Zero-API calibration for the finite spark hypothesis worlds.

This module exhausts every one of the 256 possible targets on explicitly
retired world seeds.  Query policies see only the current version space and
the response partitions induced on the ordered evidence pool.  The realized
target determines the oracle response, but is never an input to query
selection.  Private-test points are not consulted by either selector.

The implementation reuses :class:`src.spark_compressor.SparkCompressor` to
construct the semantic hypothesis bank, then precomputes its 256-by-256 oracle
response table.  Calibration is therefore exactly aligned with the compressor
while avoiding repeated AST parsing and execution during exhaustive sweeps.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .spark_compressor import OracleResponse, SemanticHypothesis, SparkCompressor
from .spark_world import SPARK_BANK_SIZE, generate_spark_world


# These seeds have already been used for operational calibration or development
# in the preceding temperature experiments and can never become confirmatory
# evidence.  Keeping the literal list here makes the zero-API calibration scope
# visible without consulting another mutable registry.
RETIRED_SPARK_WORLD_SEEDS = (
    1000,
    1001,
    1002,
    1003,
    1004,
    1005,
    1006,
    1007,
    1008,
)
CALIBRATION_K_VALUES = tuple(range(5))
SHORTEST_PARENT = "shortest_parent"
RESPONSE_ENTROPY = "response_entropy"
SELECTORS = (SHORTEST_PARENT, RESPONSE_ENTROPY)

SelectorName = Literal["shortest_parent", "response_entropy"]


@dataclass(frozen=True)
class CalibrationContext:
    """Target-free, precomputed state shared by all 256 target trajectories."""

    world_seed: int
    world_hash: str
    hypotheses: tuple[SemanticHypothesis, ...]
    raw_to_semantic: tuple[int, ...]
    evidence_points: tuple[tuple[int, int, int], ...]
    evidence_domain_indices: tuple[int, ...]
    response_matrix: tuple[tuple[OracleResponse, ...], ...]
    initial_consensus_indices: tuple[int, ...]

    @property
    def initial_version(self) -> tuple[int, ...]:
        return tuple(range(len(self.hypotheses)))

    @property
    def target_count(self) -> int:
        return len(self.raw_to_semantic)


@dataclass(frozen=True)
class CalibrationStep:
    """One target response and exact version-space update."""

    round_index: int
    query_semantic_index: int
    query_raw_index: int
    query_canonical_hash: str
    response: OracleResponse
    partition_entropy_bits: float
    version_indices_before: tuple[int, ...]
    version_indices_after: tuple[int, ...]
    certified_fact_count: int

    @property
    def N_before(self) -> int:
        return len(self.version_indices_before)

    @property
    def N_after(self) -> int:
        return len(self.version_indices_after)

    @property
    def contraction_bits(self) -> float:
        return math.log2(self.N_before / self.N_after)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_index": self.round_index,
            "query_semantic_index": self.query_semantic_index,
            "query_raw_index": self.query_raw_index,
            "query_canonical_hash": self.query_canonical_hash,
            "response": self.response.to_dict(),
            "partition_entropy_bits": self.partition_entropy_bits,
            "N_before": self.N_before,
            "N_after": self.N_after,
            "contraction_bits": self.contraction_bits,
            "certified_fact_count": self.certified_fact_count,
        }


@dataclass(frozen=True)
class CalibrationTrajectory:
    """A deterministic trajectory for one raw target index and selector."""

    world_seed: int
    target_index: int
    target_semantic_index: int
    selector: SelectorName
    max_queries: int
    initial_version: tuple[int, ...]
    initial_consensus_indices: tuple[int, ...]
    steps: tuple[CalibrationStep, ...]

    @property
    def final_version(self) -> tuple[int, ...]:
        if self.steps:
            return self.steps[-1].version_indices_after
        return self.initial_version

    @property
    def final_version_size(self) -> int:
        return len(self.final_version)

    @property
    def singleton(self) -> bool:
        return self.final_version_size == 1

    @property
    def direct_hit(self) -> bool:
        return bool(
            self.steps
            and self.steps[0].query_semantic_index == self.target_semantic_index
        )

    @property
    def contraction_bits(self) -> float:
        return math.log2(len(self.initial_version) / self.final_version_size)

    @property
    def certified_fact_count(self) -> int:
        return self.steps[-1].certified_fact_count if self.steps else 0

    @property
    def version_sizes(self) -> tuple[int, ...]:
        return (len(self.initial_version), *(step.N_after for step in self.steps))

    def to_dict(self) -> dict[str, Any]:
        return {
            "world_seed": self.world_seed,
            "target_index": self.target_index,
            "target_semantic_index": self.target_semantic_index,
            "selector": self.selector,
            "max_queries": self.max_queries,
            "N_t": self.version_sizes,
            "singleton": self.singleton,
            "direct_hit": self.direct_hit,
            "contraction_bits": self.contraction_bits,
            "certified_fact_count": self.certified_fact_count,
            "steps": tuple(step.to_dict() for step in self.steps),
        }


def _semantic_oracle_response(
    context: CalibrationContext,
    target_semantic_index: int,
    query_semantic_index: int,
) -> OracleResponse:
    target = context.hypotheses[target_semantic_index]
    query = context.hypotheses[query_semantic_index]
    for evidence_index, domain_index in enumerate(context.evidence_domain_indices):
        label = target.behavior[domain_index]
        if label != query.behavior[domain_index]:
            return OracleResponse.first_mismatch(
                evidence_index,
                context.evidence_points[evidence_index],
                label,
            )
    return OracleResponse.match()


def _consensus_indices(
    context: CalibrationContext,
    version_indices: Sequence[int],
) -> tuple[int, ...]:
    if not version_indices:
        raise ValueError("version space cannot be empty")
    first = context.hypotheses[version_indices[0]].behavior
    return tuple(
        domain_index
        for domain_index, value in enumerate(first)
        if all(
            context.hypotheses[index].behavior[domain_index] == value
            for index in version_indices[1:]
        )
    )


def build_calibration_context(world_seed: int) -> CalibrationContext:
    """Precompute a target-independent response table for one retired world."""

    if type(world_seed) is not int:
        raise TypeError("world_seed must be an integer")
    # Target seed zero is used only to obtain a correctly labelled object for
    # SparkCompressor construction.  No target-dependent field is retained in
    # query selection or in the response table below.
    world = generate_spark_world(world_seed, target_seed=0)
    compressor = SparkCompressor(world)
    hypotheses = compressor.hypotheses
    if len(hypotheses) != SPARK_BANK_SIZE:
        raise ValueError("calibration requires exactly 256 semantic hypotheses")

    raw_to_semantic = [-1] * len(world.hypotheses)
    for semantic_index, hypothesis in enumerate(hypotheses):
        for raw_index in hypothesis.raw_indices:
            raw_to_semantic[raw_index] = semantic_index
    if any(index < 0 for index in raw_to_semantic):
        raise ValueError("semantic bank did not account for every raw hypothesis")

    domain_lookup = {point: index for index, point in enumerate(compressor.domain)}
    evidence_domain_indices = tuple(
        domain_lookup[point] for point in compressor.evidence_points
    )
    provisional = CalibrationContext(
        world_seed=world_seed,
        world_hash=world.world_hash,
        hypotheses=hypotheses,
        raw_to_semantic=tuple(raw_to_semantic),
        evidence_points=compressor.evidence_points,
        evidence_domain_indices=evidence_domain_indices,
        response_matrix=(),
        initial_consensus_indices=(),
    )
    response_matrix = tuple(
        tuple(
            _semantic_oracle_response(provisional, target_index, query_index)
            for query_index in provisional.initial_version
        )
        for target_index in provisional.initial_version
    )
    initial_consensus = _consensus_indices(provisional, provisional.initial_version)
    return CalibrationContext(
        world_seed=provisional.world_seed,
        world_hash=provisional.world_hash,
        hypotheses=provisional.hypotheses,
        raw_to_semantic=provisional.raw_to_semantic,
        evidence_points=provisional.evidence_points,
        evidence_domain_indices=provisional.evidence_domain_indices,
        response_matrix=response_matrix,
        initial_consensus_indices=initial_consensus,
    )


def response_partition(
    context: CalibrationContext,
    version_indices: Sequence[int],
    query_semantic_index: int,
) -> Mapping[OracleResponse, tuple[int, ...]]:
    """Partition a version space by complete possible oracle responses."""

    version = tuple(version_indices)
    if not version:
        raise ValueError("version space cannot be empty")
    if query_semantic_index not in version:
        raise ValueError("query must be a member of the current version space")
    groups: dict[OracleResponse, list[int]] = {}
    for target_index in version:
        response = context.response_matrix[target_index][query_semantic_index]
        groups.setdefault(response, []).append(target_index)
    return {response: tuple(indices) for response, indices in groups.items()}


def partition_entropy_bits(partition_sizes: Iterable[int]) -> float:
    """Shannon entropy of a response partition under a uniform version prior."""

    sizes = tuple(partition_sizes)
    if not sizes or any(type(size) is not int or size <= 0 for size in sizes):
        raise ValueError("partition sizes must be positive integers")
    total = sum(sizes)
    return -sum((size / total) * math.log2(size / total) for size in sizes)


def response_partition_entropy_bits(
    context: CalibrationContext,
    version_indices: Sequence[int],
    query_semantic_index: int,
) -> float:
    partition = response_partition(context, version_indices, query_semantic_index)
    return partition_entropy_bits(len(group) for group in partition.values())


def select_shortest_parent(
    context: CalibrationContext,
    version_indices: Sequence[int],
) -> int:
    """Frozen baseline: shortest AST, then canonical hash."""

    version = tuple(version_indices)
    if not version:
        raise ValueError("version space cannot be empty")
    return min(version, key=lambda index: context.hypotheses[index].rank_key)


def select_response_entropy_query(
    context: CalibrationContext,
    version_indices: Sequence[int],
) -> int:
    """Maximize response entropy without observing the realized target."""

    version = tuple(version_indices)
    if not version:
        raise ValueError("version space cannot be empty")
    return min(
        version,
        key=lambda index: (
            -response_partition_entropy_bits(context, version, index),
            context.hypotheses[index].rank_key,
        ),
    )


def _select_query(
    context: CalibrationContext,
    version_indices: tuple[int, ...],
    selector: SelectorName,
) -> int:
    if selector == SHORTEST_PARENT:
        return select_shortest_parent(context, version_indices)
    if selector == RESPONSE_ENTROPY:
        return select_response_entropy_query(context, version_indices)
    raise ValueError(f"unknown selector: {selector!r}")


def run_calibration_trajectory(
    context: CalibrationContext,
    target_index: int,
    selector: SelectorName,
    *,
    max_queries: int = 4,
    selection_cache: dict[tuple[str, tuple[int, ...]], int] | None = None,
) -> CalibrationTrajectory:
    """Run one target trajectory with no model calls and no private-test access."""

    if type(target_index) is not int or not 0 <= target_index < context.target_count:
        raise ValueError("target_index is out of range")
    if type(max_queries) is not int or max_queries < 0:
        raise ValueError("max_queries must be a non-negative integer")
    if selector not in SELECTORS:
        raise ValueError(f"unknown selector: {selector!r}")

    target_semantic_index = context.raw_to_semantic[target_index]
    version = context.initial_version
    initial_facts = set(context.initial_consensus_indices)
    steps: list[CalibrationStep] = []
    for round_index in range(max_queries):
        if len(version) == 1:
            break
        cache_key = (selector, version)
        if selection_cache is not None and cache_key in selection_cache:
            query_index = selection_cache[cache_key]
        else:
            query_index = _select_query(context, version, selector)
            if selection_cache is not None:
                selection_cache[cache_key] = query_index
        entropy = response_partition_entropy_bits(context, version, query_index)
        observed = context.response_matrix[target_semantic_index][query_index]
        updated = tuple(
            candidate_target
            for candidate_target in version
            if context.response_matrix[candidate_target][query_index] == observed
        )
        if not updated or target_semantic_index not in updated:
            raise RuntimeError("calibration update failed to retain the target")
        consensus = set(_consensus_indices(context, updated))
        steps.append(
            CalibrationStep(
                round_index=round_index,
                query_semantic_index=query_index,
                query_raw_index=context.hypotheses[query_index].raw_indices[0],
                query_canonical_hash=context.hypotheses[query_index].canonical_hash,
                response=observed,
                partition_entropy_bits=entropy,
                version_indices_before=version,
                version_indices_after=updated,
                certified_fact_count=len(consensus - initial_facts),
            )
        )
        version = updated
    return CalibrationTrajectory(
        world_seed=context.world_seed,
        target_index=target_index,
        target_semantic_index=target_semantic_index,
        selector=selector,
        max_queries=max_queries,
        initial_version=context.initial_version,
        initial_consensus_indices=context.initial_consensus_indices,
        steps=tuple(steps),
    )


def replay_transcript(
    context: CalibrationContext,
    trajectory: CalibrationTrajectory,
) -> tuple[int, ...]:
    """Strictly replay stored queries/responses and return the terminal version."""

    version = context.initial_version
    target = trajectory.target_semantic_index
    for round_index, step in enumerate(trajectory.steps):
        if step.round_index != round_index or step.version_indices_before != version:
            raise ValueError("transcript version history is inconsistent")
        selected = _select_query(context, version, trajectory.selector)
        if selected != step.query_semantic_index:
            raise ValueError("transcript query differs from deterministic selector")
        observed = context.response_matrix[target][selected]
        if observed != step.response:
            raise ValueError("transcript response differs from target response")
        version = tuple(
            candidate_target
            for candidate_target in version
            if context.response_matrix[candidate_target][selected] == observed
        )
        if version != step.version_indices_after:
            raise ValueError("transcript update differs from recorded version")
    return version


def _prefix_metrics(
    trajectory: CalibrationTrajectory,
    k: int,
) -> tuple[int, bool, float, int, bool]:
    steps = trajectory.steps[:k]
    terminal = steps[-1].version_indices_after if steps else trajectory.initial_version
    terminal_size = len(terminal)
    singleton = terminal_size == 1
    contraction = math.log2(len(trajectory.initial_version) / terminal_size)
    certified = steps[-1].certified_fact_count if steps else 0
    direct_hit = bool(
        k > 0
        and trajectory.steps
        and trajectory.steps[0].query_semantic_index == trajectory.target_semantic_index
    )
    return terminal_size, singleton, contraction, certified, direct_hit


def _summarize(
    trajectories: Sequence[CalibrationTrajectory],
    k_values: Sequence[int],
) -> list[dict[str, Any]]:
    if not trajectories:
        raise ValueError("cannot summarize zero trajectories")
    summaries: list[dict[str, Any]] = []
    for k in k_values:
        rows = [_prefix_metrics(trajectory, k) for trajectory in trajectories]
        count = len(rows)
        singleton_count = sum(row[1] for row in rows)
        direct_hit_count = sum(row[4] for row in rows)
        summaries.append(
            {
                "K": k,
                "trajectory_count": count,
                "singleton_count": singleton_count,
                "singleton_rate": singleton_count / count,
                "singleton_without_direct_hit_count": sum(
                    singleton and not direct
                    for _, singleton, _, _, direct in rows
                ),
                "mean_terminal_N": sum(row[0] for row in rows) / count,
                "mean_contraction_bits": sum(row[2] for row in rows) / count,
                "mean_certified_fact_count": sum(row[3] for row in rows) / count,
                "direct_hit_count": direct_hit_count,
                "direct_hit_rate": direct_hit_count / count,
            }
        )
    return summaries


def _headroom(
    baseline: Sequence[Mapping[str, Any]],
    benchmark: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base, comparison in zip(baseline, benchmark, strict=True):
        if base["K"] != comparison["K"]:
            raise ValueError("selector summaries use different K values")
        rows.append(
            {
                "K": base["K"],
                "singleton_rate_gain": (
                    comparison["singleton_rate"] - base["singleton_rate"]
                ),
                # Positive means the benchmark leaves fewer hypotheses.
                "mean_terminal_N_reduction": (
                    base["mean_terminal_N"] - comparison["mean_terminal_N"]
                ),
                "mean_contraction_bits_gain": (
                    comparison["mean_contraction_bits"]
                    - base["mean_contraction_bits"]
                ),
                "mean_certified_fact_count_gain": (
                    comparison["mean_certified_fact_count"]
                    - base["mean_certified_fact_count"]
                ),
                "direct_hit_rate_gain": (
                    comparison["direct_hit_rate"] - base["direct_hit_rate"]
                ),
            }
        )
    return rows


def run_offline_calibration(
    *,
    world_seeds: Sequence[int] = RETIRED_SPARK_WORLD_SEEDS,
    k_values: Sequence[int] = CALIBRATION_K_VALUES,
) -> dict[str, Any]:
    """Exhaust all 256 targets per world and return aggregate JSON-ready results."""

    seeds = tuple(world_seeds)
    ks = tuple(k_values)
    if not seeds or any(type(seed) is not int for seed in seeds):
        raise ValueError("world_seeds must be a non-empty integer sequence")
    if not ks or any(type(k) is not int or k < 0 for k in ks):
        raise ValueError("k_values must be non-empty non-negative integers")
    max_queries = max(ks)
    trajectories: dict[str, list[CalibrationTrajectory]] = {
        selector: [] for selector in SELECTORS
    }
    per_world: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for seed in seeds:
        context = build_calibration_context(seed)
        selection_cache: dict[tuple[str, tuple[int, ...]], int] = {}
        world_trajectories: dict[str, list[CalibrationTrajectory]] = {
            selector: [] for selector in SELECTORS
        }
        for target_index in range(context.target_count):
            for selector in SELECTORS:
                trajectory = run_calibration_trajectory(
                    context,
                    target_index,
                    selector,  # type: ignore[arg-type]
                    max_queries=max_queries,
                    selection_cache=selection_cache,
                )
                trajectories[selector].append(trajectory)
                world_trajectories[selector].append(trajectory)
        per_world[str(seed)] = {
            selector: _summarize(world_trajectories[selector], ks)
            for selector in SELECTORS
        }

    baseline = _summarize(trajectories[SHORTEST_PARENT], ks)
    benchmark = _summarize(trajectories[RESPONSE_ENTROPY], ks)
    return {
        "schema_version": 1,
        "kind": "spark-zero-api-offline-calibration",
        "world_seeds": list(seeds),
        "target_indices_per_world": SPARK_BANK_SIZE,
        "K_values": list(ks),
        "protocol": {
            "model_calls": 0,
            "target_selection_exhaustive": True,
            "query_selector_receives_realized_target": False,
            "query_selector_uses_private_test": False,
            "response_entropy_prior": "uniform_over_current_version_space",
        },
        "selectors": {
            SHORTEST_PARENT: {"aggregate_by_K": baseline},
            RESPONSE_ENTROPY: {
                "aggregate_by_K": benchmark,
                "headroom_vs_shortest_parent_by_K": _headroom(
                    baseline, benchmark
                ),
            },
        },
        "per_world": per_world,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--world-seeds", nargs="+", type=int)
    args = parser.parse_args(argv)
    result = run_offline_calibration(
        world_seeds=(
            RETIRED_SPARK_WORLD_SEEDS
            if args.world_seeds is None
            else tuple(args.world_seeds)
        )
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CALIBRATION_K_VALUES",
    "RESPONSE_ENTROPY",
    "RETIRED_SPARK_WORLD_SEEDS",
    "SELECTORS",
    "SHORTEST_PARENT",
    "CalibrationContext",
    "CalibrationStep",
    "CalibrationTrajectory",
    "build_calibration_context",
    "main",
    "partition_entropy_bits",
    "replay_transcript",
    "response_partition",
    "response_partition_entropy_bits",
    "run_calibration_trajectory",
    "run_offline_calibration",
    "select_response_entropy_query",
    "select_shortest_parent",
]
