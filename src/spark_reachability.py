"""Zero-API calibration of motif-reachable spark queries.

The earlier bank-wide calibration asks whether a useful query exists anywhere
in the finite hypothesis bank.  This module asks the narrower operational
question: can a frozen arithmetic motif and replayable edit actually reach a
query that changes the induced version-space partition and leaves measurable
four-query headroom?

Eligibility is target-independent.  A child is selected only by its first
response-partition entropy; four-query outcomes are computed afterwards over
all 256 possible targets.  The first query may be outside the bank, while the
remaining three queries use the frozen shortest-member rule.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import dsl
from .spark_calibration import (
    RETIRED_SPARK_WORLD_SEEDS,
    CalibrationContext,
    build_calibration_context,
    partition_entropy_bits,
    select_shortest_parent,
)
from .spark_compressor import OracleResponse
from .spark_lineage import (
    MOTIF_STRATA,
    LineageRecord,
    enumerate_reachable_children,
)
from .spark_world import SPARK_BANK_SIZE, generate_spark_world


MAX_QUERIES = 4
MIN_ELIGIBLE_CHILDREN = 16
MIN_CHILDREN_PER_STRATUM = 4
MIN_OPERATIONAL_PARTITIONS = 16
MIN_INDUCED_CELL_CHANGE_RATE = 0.20
BASELINE_SINGLETON_RATE_MIN = 0.10
BASELINE_SINGLETON_RATE_MAX = 0.70
REACHABLE_SINGLETON_RATE_MIN = 0.80
REACHABLE_HEADROOM_MIN = 0.20
DIRECT_HIT_RATE_MAX = 0.05


@dataclass(frozen=True)
class QueryProfile:
    """Complete first-query response partition over all possible targets."""

    responses: tuple[OracleResponse, ...]
    cells_by_target: tuple[tuple[int, ...], ...]
    partition_cells: tuple[tuple[int, ...], ...]
    partition_sha256: str
    entropy_bits: float
    raw_response_change_rate: float
    induced_cell_change_rate: float


@dataclass(frozen=True)
class OutcomeSummary:
    trajectory_count: int
    singleton_count: int
    singleton_without_direct_hit_count: int
    direct_hit_count: int
    terminal_n_sum: int
    contraction_bits_sum: float
    certified_fact_count_sum: int
    N1_histogram: tuple[tuple[int, int], ...]
    N4_histogram: tuple[tuple[int, int], ...]

    @property
    def singleton_rate(self) -> float:
        return self.singleton_count / self.trajectory_count

    @property
    def singleton_without_direct_hit_rate(self) -> float:
        return self.singleton_without_direct_hit_count / self.trajectory_count

    @property
    def direct_hit_rate(self) -> float:
        return self.direct_hit_count / self.trajectory_count

    @property
    def mean_terminal_N(self) -> float:
        return self.terminal_n_sum / self.trajectory_count

    @property
    def mean_contraction_bits(self) -> float:
        return self.contraction_bits_sum / self.trajectory_count

    @property
    def mean_certified_fact_count(self) -> float:
        return self.certified_fact_count_sum / self.trajectory_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "trajectory_count": self.trajectory_count,
            "singleton_count": self.singleton_count,
            "singleton_rate": self.singleton_rate,
            "singleton_without_direct_hit_count": (
                self.singleton_without_direct_hit_count
            ),
            "singleton_without_direct_hit_rate": (
                self.singleton_without_direct_hit_rate
            ),
            "direct_hit_count": self.direct_hit_count,
            "direct_hit_rate": self.direct_hit_rate,
            "mean_terminal_N": self.mean_terminal_N,
            "terminal_N_sum": self.terminal_n_sum,
            "mean_contraction_bits": self.mean_contraction_bits,
            "contraction_bits_sum": self.contraction_bits_sum,
            "mean_certified_fact_count": self.mean_certified_fact_count,
            "certified_fact_count_sum": self.certified_fact_count_sum,
            "N1_histogram": {str(key): value for key, value in self.N1_histogram},
            "N4_histogram": {str(key): value for key, value in self.N4_histogram},
        }


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _response_vector(
    context: CalibrationContext,
    query_ast: dsl.Expr,
) -> tuple[OracleResponse, ...]:
    query_values = tuple(dsl.evaluate(query_ast, p) for p in context.evidence_points)
    responses: list[OracleResponse] = []
    for target in context.hypotheses:
        for evidence_index, (point, domain_index, query_value) in enumerate(
            zip(
                context.evidence_points,
                context.evidence_domain_indices,
                query_values,
                strict=True,
            )
        ):
            label = target.behavior[domain_index]
            if label != query_value:
                responses.append(
                    OracleResponse.first_mismatch(evidence_index, point, label)
                )
                break
        else:
            responses.append(OracleResponse.match())
    return tuple(responses)


def _partition(
    responses: Sequence[OracleResponse],
) -> tuple[tuple[tuple[int, ...], ...], tuple[tuple[int, ...], ...]]:
    groups: dict[OracleResponse, list[int]] = {}
    for target_index, response in enumerate(responses):
        groups.setdefault(response, []).append(target_index)
    cells = tuple(sorted(tuple(indices) for indices in groups.values()))
    by_target: list[tuple[int, ...] | None] = [None] * len(responses)
    for cell in cells:
        for target_index in cell:
            by_target[target_index] = cell
    if any(cell is None for cell in by_target):
        raise RuntimeError("response partition did not cover every target")
    return tuple(by_target), cells  # type: ignore[arg-type,return-value]


def build_query_profile(
    context: CalibrationContext,
    query_ast: dsl.Expr,
    *,
    parent_profile: QueryProfile | None = None,
) -> QueryProfile:
    """Build a target-exhaustive operational profile for one query."""

    responses = _response_vector(context, query_ast)
    cells_by_target, cells = _partition(responses)
    if parent_profile is None:
        raw_change = 0.0
        cell_change = 0.0
    else:
        if len(parent_profile.responses) != len(responses):
            raise ValueError("parent and child profiles use different target banks")
        raw_change = sum(
            left != right
            for left, right in zip(responses, parent_profile.responses, strict=True)
        ) / len(responses)
        cell_change = sum(
            left != right
            for left, right in zip(
                cells_by_target, parent_profile.cells_by_target, strict=True
            )
        ) / len(responses)
    return QueryProfile(
        responses=responses,
        cells_by_target=cells_by_target,
        partition_cells=cells,
        partition_sha256=_sha256_json(cells),
        entropy_bits=partition_entropy_bits(len(cell) for cell in cells),
        raw_response_change_rate=raw_change,
        induced_cell_change_rate=cell_change,
    )


def _consensus_indices(
    context: CalibrationContext,
    version: tuple[int, ...],
) -> frozenset[int]:
    first = context.hypotheses[version[0]].behavior
    return frozenset(
        domain_index
        for domain_index, value in enumerate(first)
        if all(
            context.hypotheses[index].behavior[domain_index] == value
            for index in version[1:]
        )
    )


def evaluate_query_profile(
    context: CalibrationContext,
    profile: QueryProfile,
    query_full_behavior: tuple[int, ...],
    *,
    remaining_queries: int = MAX_QUERIES - 1,
    tail_cache: dict[tuple[tuple[int, ...], int, int], tuple[int, ...]] | None = None,
    consensus_cache: dict[tuple[int, ...], frozenset[int]] | None = None,
) -> OutcomeSummary:
    """Evaluate child-first plus frozen shortest-member continuation."""

    if len(profile.responses) != len(context.hypotheses):
        raise ValueError("profile and calibration context use different banks")
    if len(query_full_behavior) != len(context.hypotheses[0].behavior):
        raise ValueError("query behavior does not cover the complete domain")
    if remaining_queries < 0:
        raise ValueError("remaining_queries cannot be negative")
    tail = {} if tail_cache is None else tail_cache
    facts = {} if consensus_cache is None else consensus_cache
    initial_facts = frozenset(context.initial_consensus_indices)

    def finish(
        version: tuple[int, ...], target: int, rounds_left: int
    ) -> tuple[int, ...]:
        key = (version, target, rounds_left)
        cached = tail.get(key)
        if cached is not None:
            return cached
        current = version
        for _ in range(rounds_left):
            if len(current) == 1:
                break
            query_index = select_shortest_parent(context, current)
            observed = context.response_matrix[target][query_index]
            current = tuple(
                index
                for index in current
                if context.response_matrix[index][query_index] == observed
            )
            if not current or target not in current:
                raise RuntimeError("reachable trajectory failed to retain target")
        tail[key] = current
        return current

    n1_counts: Counter[int] = Counter()
    n4_counts: Counter[int] = Counter()
    singleton_count = 0
    mediated_count = 0
    direct_count = 0
    terminal_n_sum = 0
    contraction_sum = 0.0
    certified_sum = 0
    for target_index, first_cell in enumerate(profile.cells_by_target):
        if target_index not in first_cell:
            raise RuntimeError("first-query response cell lost the target")
        terminal = finish(first_cell, target_index, remaining_queries)
        direct = query_full_behavior == context.hypotheses[target_index].behavior
        singleton = len(terminal) == 1
        consensus = facts.get(terminal)
        if consensus is None:
            consensus = _consensus_indices(context, terminal)
            facts[terminal] = consensus
        n1_counts[len(first_cell)] += 1
        n4_counts[len(terminal)] += 1
        singleton_count += singleton
        direct_count += direct
        mediated_count += singleton and not direct
        terminal_n_sum += len(terminal)
        contraction_sum += math.log2(SPARK_BANK_SIZE / len(terminal))
        certified_sum += len(consensus - initial_facts)
    return OutcomeSummary(
        trajectory_count=len(context.hypotheses),
        singleton_count=singleton_count,
        singleton_without_direct_hit_count=mediated_count,
        direct_hit_count=direct_count,
        terminal_n_sum=terminal_n_sum,
        contraction_bits_sum=contraction_sum,
        certified_fact_count_sum=certified_sum,
        N1_histogram=tuple(sorted(n1_counts.items())),
        N4_histogram=tuple(sorted(n4_counts.items())),
    )


def _combine_outcomes(rows: Sequence[OutcomeSummary]) -> OutcomeSummary:
    if not rows:
        raise ValueError("cannot combine zero outcome summaries")
    n1: Counter[int] = Counter()
    n4: Counter[int] = Counter()
    for row in rows:
        n1.update(dict(row.N1_histogram))
        n4.update(dict(row.N4_histogram))
    return OutcomeSummary(
        trajectory_count=sum(row.trajectory_count for row in rows),
        singleton_count=sum(row.singleton_count for row in rows),
        singleton_without_direct_hit_count=sum(
            row.singleton_without_direct_hit_count for row in rows
        ),
        direct_hit_count=sum(row.direct_hit_count for row in rows),
        terminal_n_sum=sum(row.terminal_n_sum for row in rows),
        contraction_bits_sum=sum(row.contraction_bits_sum for row in rows),
        certified_fact_count_sum=sum(row.certified_fact_count_sum for row in rows),
        N1_histogram=tuple(sorted(n1.items())),
        N4_histogram=tuple(sorted(n4.items())),
    )


def _profile_sort_key(record: LineageRecord, profile: QueryProfile) -> tuple[Any, ...]:
    return (
        -profile.entropy_bits,
        dsl.node_count(record.child_ast),
        record.child_canonical_hash,
        record.motif_stratum,
        record.motif_id,
        record.action_hash,
    )


def calibrate_reachable_world(world_seed: int) -> dict[str, Any]:
    """Calibrate every structurally reachable child in one retired world."""

    world = generate_spark_world(world_seed, target_seed=0)
    context = build_calibration_context(world_seed)
    records = enumerate_reachable_children(world)
    parent_index = select_shortest_parent(context, context.initial_version)
    parent_ast = context.hypotheses[parent_index].ast
    parent_profile = build_query_profile(context, parent_ast)
    parent_behavior = context.hypotheses[parent_index].behavior

    profile_cache: dict[str, tuple[QueryProfile, tuple[int, ...]]] = {}

    def profile_for(ast: dsl.Expr) -> tuple[QueryProfile, tuple[int, ...]]:
        behavior = dsl.behavior_vector(ast, world.domain)
        key = hashlib.sha256(bytes(behavior)).hexdigest()
        cached = profile_cache.get(key)
        if cached is None:
            cached = (
                build_query_profile(context, ast, parent_profile=parent_profile),
                behavior,
            )
            profile_cache[key] = cached
        return cached

    eligible: list[
        tuple[
            LineageRecord,
            QueryProfile,
            tuple[int, ...],
            tuple[tuple[str, QueryProfile, tuple[int, ...]], ...],
        ]
    ] = []
    exclusion_counts: Counter[str] = Counter()
    for record in records:
        profile, behavior = profile_for(record.child_ast)
        if profile.induced_cell_change_rate < MIN_INDUCED_CELL_CHANGE_RATE:
            exclusion_counts["focal_rho_V_below_threshold"] += 1
            continue
        accepted_replacements: list[tuple[str, QueryProfile, tuple[int, ...]]] = []
        replacement_partition_hashes: set[str] = set()
        for replacement in record.matched_replacements:
            replacement_profile, replacement_behavior = profile_for(
                replacement.child_ast
            )
            if (
                replacement_profile.induced_cell_change_rate
                < MIN_INDUCED_CELL_CHANGE_RATE
            ):
                continue
            if replacement_profile.partition_sha256 == profile.partition_sha256:
                continue
            if replacement_profile.partition_sha256 in replacement_partition_hashes:
                continue
            accepted_replacements.append(
                (replacement.motif_id, replacement_profile, replacement_behavior)
            )
            replacement_partition_hashes.add(replacement_profile.partition_sha256)
        if len(accepted_replacements) < 2:
            exclusion_counts["fewer_than_two_operational_replacements"] += 1
            continue
        eligible.append(
            (record, profile, behavior, tuple(accepted_replacements[:2]))
        )

    operationally_eligible_before_dedup = len(eligible)
    # Only now is semantic de-duplication safe: retain the deterministic best
    # operationally control-ready frame for each stratum/child behavior.
    deduplicated: dict[
        tuple[str, str],
        tuple[
            LineageRecord,
            QueryProfile,
            tuple[int, ...],
            tuple[tuple[str, QueryProfile, tuple[int, ...]], ...],
        ],
    ] = {}
    for item in eligible:
        record, profile, _, _ = item
        key = (record.motif_stratum, record.child_behavior_hash)
        incumbent = deduplicated.get(key)
        rank = _profile_sort_key(record, profile)[1:]
        if incumbent is None or rank < _profile_sort_key(
            incumbent[0], incumbent[1]
        )[1:]:
            deduplicated[key] = item
    eligible = list(deduplicated.values())
    exclusion_counts["post_operational_semantic_duplicate"] = (
        operationally_eligible_before_dedup - len(eligible)
    )

    unique_behaviors = {record.child_behavior_hash for record, *_ in eligible}
    per_stratum = {
        stratum: len(
            {
                record.child_behavior_hash
                for record, *_ in eligible
                if record.motif_stratum == stratum
            }
        )
        for stratum in MOTIF_STRATA
    }
    unique_partitions = {profile.partition_sha256 for _, profile, *_ in eligible}
    structure_checks = {
        "minimum_global_semantic_children": (
            len(unique_behaviors) >= MIN_ELIGIBLE_CHILDREN
        ),
        "all_four_motif_strata": all(
            per_stratum[stratum] >= MIN_CHILDREN_PER_STRATUM
            for stratum in MOTIF_STRATA
        ),
        "minimum_operational_partitions": (
            len(unique_partitions) >= MIN_OPERATIONAL_PARTITIONS
        ),
        "two_matched_operational_replacements_per_child": all(
            len(replacements) >= 2 for *_, replacements in eligible
        ),
    }
    structure_passed = all(structure_checks.values())

    outcome_cache: dict[tuple[str, tuple[int, ...]], OutcomeSummary] = {}

    def outcome_for(
        profile: QueryProfile, behavior: tuple[int, ...]
    ) -> OutcomeSummary:
        cache_key = (profile.partition_sha256, behavior)
        cached = outcome_cache.get(cache_key)
        if cached is None:
            cached = evaluate_query_profile(
                context,
                profile,
                behavior,
                # Caches are intentionally profile-local.  Sharing them across
                # hundreds of children saves little computation but retains
                # large version tuples long enough to exhaust a research
                # workstation during the nine-world exhaustive sweep.
                tail_cache={},
                consensus_cache={},
            )
            outcome_cache[cache_key] = cached
        return cached

    parent_outcome = outcome_for(parent_profile, parent_behavior)
    child_rows: list[dict[str, Any]] = []
    for record, profile, behavior, replacements in eligible:
        outcome = outcome_for(profile, behavior)
        replacement_rows: list[dict[str, Any]] = []
        for motif_id, replacement_profile, replacement_behavior in replacements:
            replacement_outcome = outcome_for(
                replacement_profile, replacement_behavior
            )
            replacement_rows.append(
                {
                    "motif_id": motif_id,
                    "partition_sha256": replacement_profile.partition_sha256,
                    "induced_cell_change_rate": (
                        replacement_profile.induced_cell_change_rate
                    ),
                    "singleton_rate": replacement_outcome.singleton_rate,
                    "focal_minus_replacement_singleton_rate": (
                        outcome.singleton_rate - replacement_outcome.singleton_rate
                    ),
                }
            )
        child_rows.append(
            {
                "lineage_hash": record.lineage_hash,
                "child_canonical_hash": record.child_canonical_hash,
                "child_behavior_hash": record.child_behavior_hash,
                "motif_id": record.motif_id,
                "motif_stratum": record.motif_stratum,
                "action_hash": record.action_hash,
                "child_node_count": dsl.node_count(record.child_ast),
                "partition_sha256": profile.partition_sha256,
                "partition_entropy_bits": profile.entropy_bits,
                "raw_response_change_rate": profile.raw_response_change_rate,
                "induced_cell_change_rate": profile.induced_cell_change_rate,
                "outcome": outcome.to_dict(),
                "singleton_rate_gain_vs_parent": (
                    outcome.singleton_rate - parent_outcome.singleton_rate
                ),
                "matched_replacements": replacement_rows,
            }
        )

    benchmark_row: dict[str, Any] | None = None
    if eligible:
        selected = min(
            eligible, key=lambda item: _profile_sort_key(item[0], item[1])
        )
        record, profile, behavior, _ = selected
        selected_outcome = outcome_for(profile, behavior)
        benchmark_row = {
            "selection_rule": (
                "maximum_first_query_partition_entropy_then_frozen_structural_key"
            ),
            "lineage_hash": record.lineage_hash,
            "child_canonical_hash": record.child_canonical_hash,
            "child_behavior_hash": record.child_behavior_hash,
            "motif_id": record.motif_id,
            "motif_stratum": record.motif_stratum,
            "partition_sha256": profile.partition_sha256,
            "partition_entropy_bits": profile.entropy_bits,
            "induced_cell_change_rate": profile.induced_cell_change_rate,
            "outcome": selected_outcome.to_dict(),
        }

    return {
        "world_seed": world_seed,
        "world_instance_hash_target_seed_zero": context.world_hash,
        "parent_canonical_hash": dsl.canonical_hash(parent_ast),
        "enumerated_control_ready_lineages": len(records),
        "operationally_eligible_lineages_before_semantic_dedup": (
            operationally_eligible_before_dedup
        ),
        "eligible_lineage_count": len(eligible),
        "eligible_unique_behavior_count": len(unique_behaviors),
        "eligible_unique_partition_count": len(unique_partitions),
        "eligible_unique_behavior_count_by_stratum": per_stratum,
        "exclusion_counts": dict(sorted(exclusion_counts.items())),
        "structure_checks": structure_checks,
        "structure_passed": structure_passed,
        "parent_outcome": parent_outcome.to_dict(),
        "benchmark": benchmark_row,
        # Full child-level outcomes are useful for a single-world diagnostic,
        # but retaining them for all nine worlds is unnecessary for the gate
        # and can exceed memory on a research workstation.  The decisive sweep
        # therefore keeps the benchmark plus the sufficient distribution
        # summary below.
        "eligible_child_outcome_distribution": {
            "child_count": len(child_rows),
            "gain_positive_count": sum(
                row["singleton_rate_gain_vs_parent"] > 0 for row in child_rows
            ),
            "gain_zero_count": sum(
                row["singleton_rate_gain_vs_parent"] == 0 for row in child_rows
            ),
            "gain_negative_count": sum(
                row["singleton_rate_gain_vs_parent"] < 0 for row in child_rows
            ),
            "minimum_singleton_rate_gain": min(
                (row["singleton_rate_gain_vs_parent"] for row in child_rows),
                default=None,
            ),
            "maximum_singleton_rate_gain": max(
                (row["singleton_rate_gain_vs_parent"] for row in child_rows),
                default=None,
            ),
        },
        "eligible_children": sorted(
            child_rows, key=lambda row: (row["motif_stratum"], row["lineage_hash"])
        ),
    }


def _outcome_from_dict(payload: Mapping[str, Any]) -> OutcomeSummary:
    return OutcomeSummary(
        trajectory_count=int(payload["trajectory_count"]),
        singleton_count=int(payload["singleton_count"]),
        singleton_without_direct_hit_count=int(
            payload["singleton_without_direct_hit_count"]
        ),
        direct_hit_count=int(payload["direct_hit_count"]),
        terminal_n_sum=int(payload["terminal_N_sum"]),
        contraction_bits_sum=float(payload["contraction_bits_sum"]),
        certified_fact_count_sum=int(payload["certified_fact_count_sum"]),
        N1_histogram=tuple(
            (int(key), int(value))
            for key, value in payload["N1_histogram"].items()
        ),
        N4_histogram=tuple(
            (int(key), int(value))
            for key, value in payload["N4_histogram"].items()
        ),
    )


def run_reachable_calibration(
    *, world_seeds: Sequence[int] = RETIRED_SPARK_WORLD_SEEDS
) -> dict[str, Any]:
    """Run the preregistered reachable gate on retired worlds only."""

    seeds = tuple(world_seeds)
    if not seeds or any(type(seed) is not int for seed in seeds):
        raise ValueError("world_seeds must be a non-empty sequence of integers")
    worlds: list[dict[str, Any]] = []
    for seed in seeds:
        world_result = calibrate_reachable_world(seed)
        if seeds == RETIRED_SPARK_WORLD_SEEDS:
            world_result["eligible_children"] = []
            world_result["eligible_children_omitted_from_decisive_artifact"] = True
        worlds.append(world_result)
        # The classifier reservoir is globally cached for speed, but cyclic
        # outcome objects from an exhaustive world should be reclaimed before
        # constructing the next one on memory-constrained research machines.
        gc.collect()
    structure_passed = all(world["structure_passed"] for world in worlds)
    benchmark_available = all(world["benchmark"] is not None for world in worlds)

    aggregate: dict[str, Any] | None = None
    performance_checks: dict[str, bool] = {
        "baseline_between_10_and_70_percent": False,
        "reachable_at_least_80_percent": False,
        "reachable_gain_at_least_20_percentage_points": False,
        "reachable_direct_hit_at_most_5_percent": False,
    }
    if benchmark_available:
        baseline = _combine_outcomes(
            [_outcome_from_dict(world["parent_outcome"]) for world in worlds]
        )
        reachable = _combine_outcomes(
            [
                _outcome_from_dict(world["benchmark"]["outcome"])
                for world in worlds
            ]
        )
        baseline_rate_exact = Fraction(
            baseline.singleton_count, baseline.trajectory_count
        )
        reachable_rate_exact = Fraction(
            reachable.singleton_count, reachable.trajectory_count
        )
        gain_exact = reachable_rate_exact - baseline_rate_exact
        gain = float(gain_exact)
        performance_checks = {
            "baseline_between_10_and_70_percent": (
                Fraction(1, 10) <= baseline_rate_exact <= Fraction(7, 10)
            ),
            "reachable_at_least_80_percent": (
                reachable_rate_exact >= Fraction(4, 5)
            ),
            "reachable_gain_at_least_20_percentage_points": (
                gain_exact >= Fraction(1, 5)
            ),
            "reachable_direct_hit_at_most_5_percent": (
                Fraction(reachable.direct_hit_count, reachable.trajectory_count)
                <= Fraction(1, 20)
            ),
        }
        aggregate = {
            "baseline": baseline.to_dict(),
            "reachable_benchmark": reachable.to_dict(),
            "singleton_rate_gain": gain,
            "singleton_rate_gain_exact": {
                "numerator": gain_exact.numerator,
                "denominator": gain_exact.denominator,
            },
            "mean_terminal_N_reduction": (
                baseline.mean_terminal_N - reachable.mean_terminal_N
            ),
            "mean_contraction_bits_gain": (
                reachable.mean_contraction_bits - baseline.mean_contraction_bits
            ),
            "mean_certified_fact_count_gain": (
                reachable.mean_certified_fact_count
                - baseline.mean_certified_fact_count
            ),
        }
    thresholds_satisfied = structure_passed and all(performance_checks.values())
    decisive_scope = seeds == RETIRED_SPARK_WORLD_SEEDS
    calibration_passed: bool | None = (
        thresholds_satisfied if decisive_scope else None
    )
    if not decisive_scope:
        decision = "diagnostic_only_nondecisive_world_scope"
    elif thresholds_satisfied:
        decision = "proceed_to_24_call_mechanism_closure"
    else:
        decision = "do_not_start_model_calls_revise_operationalization"
    return {
        "schema_version": 1,
        "kind": "spark-motif-reachable-zero-api-calibration",
        "scope": "retired_worlds_only_not_hypothesis_evidence",
        "world_seeds": list(seeds),
        "decisive_scope": decisive_scope,
        "targets_per_world": SPARK_BANK_SIZE,
        "protocol": {
            "model_calls": 0,
            "private_target_labels_or_outcomes_used_for_child_eligibility": False,
            "private_target_labels_or_outcomes_used_for_benchmark_selection": False,
            "full_domain_candidate_semantics_used_for_structural_eligibility": True,
            "full_domain_candidate_semantics_used_for_direct_hit_scoring": True,
            "first_query": "motif_edit_child",
            "remaining_queries": 3,
            "remaining_query_rule": "shortest_bank_member_then_canonical_hash",
            "benchmark_selection": "first_query_partition_entropy_only",
            "unit": "world_with_exhaustive_target_sweep",
        },
        "thresholds": {
            "minimum_eligible_unique_children_per_world": MIN_ELIGIBLE_CHILDREN,
            "minimum_unique_children_per_stratum_per_world": (
                MIN_CHILDREN_PER_STRATUM
            ),
            "minimum_operational_partitions_per_world": (
                MIN_OPERATIONAL_PARTITIONS
            ),
            "minimum_induced_cell_change_rate": (
                MIN_INDUCED_CELL_CHANGE_RATE
            ),
            "baseline_singleton_rate_interval": [
                BASELINE_SINGLETON_RATE_MIN,
                BASELINE_SINGLETON_RATE_MAX,
            ],
            "minimum_reachable_singleton_rate": REACHABLE_SINGLETON_RATE_MIN,
            "minimum_singleton_rate_gain": REACHABLE_HEADROOM_MIN,
            "maximum_direct_hit_rate": DIRECT_HIT_RATE_MAX,
        },
        "structure_passed_all_worlds": structure_passed,
        "performance_checks": performance_checks,
        "aggregate": aggregate,
        "thresholds_satisfied": thresholds_satisfied,
        "calibration_passed": calibration_passed,
        "decision": decision,
        "per_world": worlds,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--world-seeds", nargs="+", type=int)
    args = parser.parse_args(argv)
    report = run_reachable_calibration(
        world_seeds=(
            RETIRED_SPARK_WORLD_SEEDS
            if args.world_seeds is None
            else tuple(args.world_seeds)
        )
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 1 if report["calibration_passed"] is False else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BASELINE_SINGLETON_RATE_MAX",
    "BASELINE_SINGLETON_RATE_MIN",
    "DIRECT_HIT_RATE_MAX",
    "MAX_QUERIES",
    "MIN_CHILDREN_PER_STRATUM",
    "MIN_ELIGIBLE_CHILDREN",
    "MIN_INDUCED_CELL_CHANGE_RATE",
    "MIN_OPERATIONAL_PARTITIONS",
    "REACHABLE_HEADROOM_MIN",
    "REACHABLE_SINGLETON_RATE_MIN",
    "OutcomeSummary",
    "QueryProfile",
    "build_query_profile",
    "calibrate_reachable_world",
    "evaluate_query_profile",
    "main",
    "run_reachable_calibration",
]
