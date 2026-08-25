from __future__ import annotations

import copy
import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src import spark_lineage
from src.provenance import PROJECT_ROOT
from src.spark_strong_k4_scan import _raw_actions
from src.spark_strong_k4_utilization_feasibility import (
    DEGRADED_TIER,
    STRICT_TIER,
    UtilizationFeasibilityError,
    _emit_json_exclusive_0600,
    _sha256_json,
    _validate_shard,
    build_scan_shard,
    build_target_free_scan_plan,
    classify_pair_tiers,
    degraded_pair_predicate,
    deterministic_tier_matching,
    enumerate_full_motif_library,
    full_motif_library,
    merge_scan_shards,
    motif_library_identity,
    pair_candidates_for_world,
    strict_pair_predicate,
    validate_config,
    validate_scan_plan,
    validate_seed_reservation,
)


CONFIG_PATH = PROJECT_ROOT / "configs" / "spark-strong-k4-utilization-feasibility-v2.json"
RESERVATION_PATH = (
    PROJECT_ROOT / "configs" / "spark-strong-k4-utilization-feasibility-v2-seeds.json"
)
REGISTRY_PATH = PROJECT_ROOT / "configs" / "development-seed-registry.json"


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("fixture is not an object")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ConfigAndPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _read(CONFIG_PATH)
        cls.reservation = _read(RESERVATION_PATH)
        cls.registry = _read(REGISTRY_PATH)

    def test_config_reservation_and_hashes_are_exact(self) -> None:
        validate_config(self.config)
        audit = validate_seed_reservation(
            self.config,
            self.reservation,
            self.registry,
            registry_file_sha256=_sha(REGISTRY_PATH),
        )
        self.assertTrue(audit["reservation_exact"])
        self.assertEqual(motif_library_identity()["count"], 105)
        self.assertEqual(
            motif_library_identity()["sha256"],
            "a73800bb8350e2ad202d3f1dea9dce437c6b8fb232d2cc33899bfe1f7fb84e80",
        )

        tampered = copy.deepcopy(self.reservation)
        tampered["seeds"][0] += 1  # type: ignore[index]
        with self.assertRaises(UtilizationFeasibilityError):
            validate_seed_reservation(self.config, tampered, self.registry)

    def test_plan_is_target_free_and_binds_1024_candidates(self) -> None:
        with (
            mock.patch(
                "src.spark_strong_k4_utilization_feasibility.generate_spark_world",
                side_effect=AssertionError("target materialized"),
            ),
            mock.patch(
                "src.spark_strong_k4_utilization_feasibility.SparkCompressor",
                side_effect=AssertionError("compressor ran"),
            ),
        ):
            plan = build_target_free_scan_plan(
                self.config,
                self.reservation,
                config_file_sha256=_sha(CONFIG_PATH),
                seed_reservation_file_sha256=_sha(RESERVATION_PATH),
                source_manifest_sha256="f" * 64,
                registry=self.registry,
                registry_file_sha256=_sha(REGISTRY_PATH),
            )
        self.assertFalse(plan["target_materialized"])
        self.assertFalse(plan["compressor_run"])
        self.assertEqual(len(plan["candidates"]), 1024)
        self.assertEqual(plan["motif_library"]["count"], 105)
        self.assertEqual(plan["provider_calls_made"], 0)
        validate_scan_plan(
            self.config,
            plan,
            config_file_sha256=_sha(CONFIG_PATH),
            require_current_source=False,
        )

    def test_plan_requires_registry_audit_and_partial_merge_is_not_infeasible(self) -> None:
        with self.assertRaisesRegex(
            UtilizationFeasibilityError,
            "bound historical seed registry",
        ):
            build_target_free_scan_plan(
                self.config,
                self.reservation,
                config_file_sha256=_sha(CONFIG_PATH),
                seed_reservation_file_sha256=_sha(RESERVATION_PATH),
                source_manifest_sha256="f" * 64,
            )
        plan = build_target_free_scan_plan(
            self.config,
            self.reservation,
            config_file_sha256=_sha(CONFIG_PATH),
            seed_reservation_file_sha256=_sha(RESERVATION_PATH),
            source_manifest_sha256="f" * 64,
            registry=self.registry,
            registry_file_sha256=_sha(REGISTRY_PATH),
        )
        with self.assertRaisesRegex(
            UtilizationFeasibilityError,
            "scan_incomplete_not_infeasible",
        ):
            merge_scan_shards(
                self.config,
                plan,
                [],
                config_file_sha256=_sha(CONFIG_PATH),
                require_current_source=False,
            )

    def test_plan_digest_tamper_fails_closed(self) -> None:
        plan = build_target_free_scan_plan(
            self.config,
            self.reservation,
            config_file_sha256=_sha(CONFIG_PATH),
            seed_reservation_file_sha256=_sha(RESERVATION_PATH),
            source_manifest_sha256="f" * 64,
            registry=self.registry,
            registry_file_sha256=_sha(REGISTRY_PATH),
        )
        tampered = copy.deepcopy(plan)
        tampered["candidates"][0]["world_seed"] += 1  # type: ignore[index]
        with self.assertRaises(UtilizationFeasibilityError):
            validate_scan_plan(
                self.config,
                tampered,
                config_file_sha256=_sha(CONFIG_PATH),
                require_current_source=False,
            )

    def test_scan_requires_reviewed_plan_key_before_materialization(self) -> None:
        plan = build_target_free_scan_plan(
            self.config,
            self.reservation,
            config_file_sha256=_sha(CONFIG_PATH),
            seed_reservation_file_sha256=_sha(RESERVATION_PATH),
            source_manifest_sha256="f" * 64,
            registry=self.registry,
            registry_file_sha256=_sha(REGISTRY_PATH),
        )
        with mock.patch(
            "src.spark_strong_k4_utilization_feasibility._scan_candidate_world",
            side_effect=AssertionError("materialized before review"),
        ) as scan:
            with self.assertRaises(UtilizationFeasibilityError):
                build_scan_shard(
                    self.config,
                    plan,
                    config_file_sha256=_sha(CONFIG_PATH),
                    start_index=0,
                    count=8,
                    reviewed_plan_sha256="0" * 64,
                    require_current_source=False,
                )
        scan.assert_not_called()


class MotifAndPairTests(unittest.TestCase):
    def test_full_library_is_105_and_each_context_has_ten_actions(self) -> None:
        motifs = enumerate_full_motif_library()
        self.assertEqual(motifs, full_motif_library())
        self.assertEqual(len(motifs), 105)
        self.assertEqual(
            {
                stratum: sum(row["stratum"] == stratum for row in motifs)
                for stratum in spark_lineage.MOTIF_STRATA
            },
            {
                "affine_commutative": 21,
                "affine_directional": 42,
                "affine_multiplicative": 21,
                "pairwise_variable": 21,
            },
        )
        # The action grammar only needs a parent; no target or bank is needed.
        world = SimpleNamespace(
            hypotheses=(
                (
                    "ite",
                    ("eq", ("var", "x1"), ("const", 0)),
                    ("const", 1),
                    ("const", 0),
                ),
            )
        )
        for motif in motifs:
            actions = _raw_actions(world, str(motif["motif_id"]))
            self.assertEqual(len(actions), 10)
            self.assertEqual(len(set(actions)), 10)

    @staticmethod
    def _profile(
        motif_id: str,
        behavior: str,
        *,
        k2: list[int],
        nonconstant_k4: list[int],
        constant_k4: list[int] | None = None,
        stratum: str = "affine_commutative",
    ) -> dict[str, object]:
        constant_k4 = [] if constant_k4 is None else constant_k4
        k4 = sorted(set(nonconstant_k4) | set(constant_k4))
        actions = []
        for raw in range(10):
            flags = {
                "K1": raw in set(k2) or raw in set(k4),
                "K2": raw in set(k2),
                "K3": raw in set(k2),
                "K4_full_pool": raw in set(k4),
            }
            actions.append(
                {
                    "raw_action_index": raw,
                    "action": {
                        "operation": "replace",
                        "path": [1, 1] if raw < 5 else [1, 2],
                        "binary_operator": None,
                        "motif_side": None,
                    },
                    "endpoint_flags": flags,
                    "child_behavior_hash": ("c" * 64 if raw in set(nonconstant_k4) else "d" * 64),
                    "child_behavior_is_constant": raw in set(constant_k4),
                    "full_pool_counterfactual_bundle_sha256": "b" * 64 if raw in set(k4) else None,
                }
            )
        return {
            "motif_id": motif_id,
            "motif_sexpr": f"(add x1 {motif_id})",
            "motif_canonical_hash": "a" * 64,
            "motif_behavior_hash": behavior * 64,
            "stratum": stratum,
            "complexity_bucket": [2, 3],
            "raw_action_count": 10,
            "k1_raw_action_indices": sorted(set(k2) | set(k4)),
            "k2_raw_action_indices": list(k2),
            "k3_raw_action_indices": list(k2),
            "k4_raw_action_indices": k4,
            "nonconstant_k4_raw_action_indices": list(nonconstant_k4),
            "constant_k4_raw_action_indices": list(constant_k4),
            "actions": actions,
        }

    def test_strict_and_degraded_truth_table_and_constant_exclusion(self) -> None:
        strict_left = self._profile("a", "a", k2=[0, 2], nonconstant_k4=[1])
        strict_right = self._profile("b", "b", k2=[3, 4], nonconstant_k4=[2])
        self.assertTrue(strict_pair_predicate(strict_left, strict_right))
        self.assertIsNotNone(classify_pair_tiers(strict_left, strict_right)[STRICT_TIER])

        degraded_left = self._profile("c", "c", k2=[0, 2], nonconstant_k4=[1, 4])
        degraded_right = self._profile("d", "d", k2=[3, 4], nonconstant_k4=[2, 5])
        tiers = classify_pair_tiers(degraded_left, degraded_right)
        self.assertIsNone(tiers[STRICT_TIER])
        self.assertIsNotNone(tiers[DEGRADED_TIER])

        constant = self._profile("e", "e", k2=[0], nonconstant_k4=[], constant_k4=[1])
        self.assertFalse(strict_pair_predicate(constant, strict_right))
        self.assertFalse(degraded_pair_predicate(constant, degraded_right))
        same_raw = self._profile("f", "f", k2=[3], nonconstant_k4=[1])
        self.assertFalse(strict_pair_predicate(strict_left, same_raw))
        unequal_k2 = self._profile("g", "g", k2=[3], nonconstant_k4=[2])
        self.assertFalse(strict_pair_predicate(strict_left, unequal_k2))

    def test_pair_candidate_order_and_determinism(self) -> None:
        profiles = [
            self._profile("a", "a", k2=[0], nonconstant_k4=[1]),
            self._profile("b", "b", k2=[1], nonconstant_k4=[2]),
            self._profile("c", "c", k2=[0], nonconstant_k4=[2]),
        ]
        first = pair_candidates_for_world(profiles)
        second = pair_candidates_for_world(list(reversed(profiles)))
        self.assertEqual(first, second)
        self.assertEqual(len(first[STRICT_TIER]), 2)


class MatchingAndBoundaryTests(unittest.TestCase):
    @staticmethod
    def _pair(
        stratum: str,
        a: str,
        b: str,
        raw_a: int = 0,
        raw_b: int = 1,
    ) -> dict[str, object]:
        return {
            "tier_id": STRICT_TIER,
            "stratum": stratum,
            "context_a_motif_id": a,
            "context_b_motif_id": b,
            "context_a_correct_raw_action_indices": [raw_a],
            "context_b_correct_raw_action_indices": [raw_b],
            "context_a_correct_actions": [],
            "context_b_correct_actions": [],
        }

    def test_matching_has_one_world_capacity_and_lexicographic_tie_break(self) -> None:
        worlds = [
            {
                "candidate_index": 0,
                "pair_candidates": {
                    STRICT_TIER: [self._pair("A", "a", "b")],
                    DEGRADED_TIER: [],
                },
            },
            {
                "candidate_index": 1,
                "pair_candidates": {
                    STRICT_TIER: [self._pair("A", "c", "d")],
                    DEGRADED_TIER: [],
                },
            },
            {
                "candidate_index": 2,
                "pair_candidates": {
                    STRICT_TIER: [self._pair("B", "e", "f")],
                    DEGRADED_TIER: [],
                },
            },
        ]
        result = deterministic_tier_matching(
            worlds,
            tier_id=STRICT_TIER,
            strata=("A", "B"),
            target_per_stratum=1,
            fallback_per_stratum=1,
        )
        self.assertTrue(result["complete"])
        selected = [row["candidate_index"] for row in result["assignments"]]
        self.assertEqual(len(selected), len(set(selected)))
        self.assertEqual(result["lexicographic_assignment_vector"], [0, 2])

    def test_matching_distinguishes_target_from_fallback_geometry(self) -> None:
        worlds = [
            {
                "candidate_index": 0,
                "pair_candidates": {
                    STRICT_TIER: [
                        self._pair("A", "a", "b"),
                        self._pair("A", "e", "f"),
                    ],
                    DEGRADED_TIER: [],
                },
            },
            {
                "candidate_index": 1,
                "pair_candidates": {
                    STRICT_TIER: [self._pair("B", "c", "d")],
                    DEGRADED_TIER: [],
                },
            },
        ]
        result = deterministic_tier_matching(
            worlds,
            tier_id=STRICT_TIER,
            strata=("A", "B"),
            target_per_stratum=2,
            fallback_per_stratum=1,
        )
        self.assertEqual(result["selection_mode"], "fallback_q")
        self.assertFalse(result["target_q_feasible"])
        self.assertTrue(result["fallback_q_feasible"])
        self.assertEqual(result["selected_q"], 1)
        self.assertEqual(
            result["maximum_exact_stratum_balanced_q_up_to_target"],
            1,
        )
        stratum_a = result["candidate_geometry"]["by_stratum"]["A"]
        self.assertEqual(stratum_a["pair_candidate_count"], 2)
        self.assertEqual(stratum_a["eligible_world_count"], 1)
        self.assertEqual(
            list(
                stratum_a[
                    "unordered_correct_raw_set_pair_world_capacity"
                ].values()
            ),
            [1],
        )
        self.assertEqual(
            result["selected_matching_geometry"]["eligible_world_count"],
            2,
        )

    def test_merge_shard_tamper_fails_closed_without_full_scan(self) -> None:
        config = _read(CONFIG_PATH)
        plan = {
            "plan_sha256": "p",
            "source_manifest_sha256": "s" * 64,
            "candidates": [{"candidate_index": i, "world_seed": i} for i in range(1024)],
        }
        shard = {
            "schema_version": 1,
            "kind": "spark-strong-k4-utilization-feasibility-shard",
            "protocol_id": "spark-strong-k4-utilization-feasibility-v2",
            "config_file_sha256": "c" * 64,
            "plan_sha256": "p",
            "source_manifest_sha256": "s" * 64,
            "candidate_range": {"start": 0, "count": 8, "end_exclusive": 8},
            "worlds": [],
            "development_only": True,
            "model_outputs_read": False,
            "provider_calls_made": 0,
            "outcome_conditioned_benchmark_construction": True,
            "shard_sha256": "0" * 64,
        }
        with self.assertRaises(UtilizationFeasibilityError):
            _validate_shard(shard, config_file_sha256="c" * 64, plan=plan)

    def test_shard_outcome_conditioning_flag_is_fail_closed(self) -> None:
        plan = {
            "plan_sha256": "p",
            "source_manifest_sha256": "s" * 64,
            "candidates": [
                {"candidate_index": index, "world_seed": index}
                for index in range(1024)
            ],
        }
        worlds = [{"candidate_index": index} for index in range(8)]
        aggregate = {"world_count": 8}
        unsigned = {
            "schema_version": 1,
            "kind": "spark-strong-k4-utilization-feasibility-shard",
            "protocol_id": "spark-strong-k4-utilization-feasibility-v2",
            "evidence_scope": "development-only",
            "config_file_sha256": "c" * 64,
            "plan_sha256": "p",
            "source_manifest_sha256": "s" * 64,
            "candidate_range": {"start": 0, "count": 8, "end_exclusive": 8},
            "aggregate": aggregate,
            "worlds": worlds,
            "development_only": True,
            "model_outputs_read": False,
            "provider_calls_made": 0,
            "outcome_conditioned_benchmark_construction": True,
        }
        shard = {**unsigned, "shard_sha256": _sha256_json(unsigned)}
        with (
            mock.patch(
                "src.spark_strong_k4_utilization_feasibility._validate_candidate_world"
            ),
            mock.patch(
                "src.spark_strong_k4_utilization_feasibility._aggregate_worlds",
                return_value=aggregate,
            ),
        ):
            self.assertEqual(
                _validate_shard(
                    shard,
                    config_file_sha256="c" * 64,
                    plan=plan,
                )[:2],
                (0, 8),
            )
            tampered_unsigned = {
                **unsigned,
                "outcome_conditioned_benchmark_construction": False,
            }
            tampered = {
                **tampered_unsigned,
                "shard_sha256": _sha256_json(tampered_unsigned),
            }
            with self.assertRaises(UtilizationFeasibilityError):
                _validate_shard(
                    tampered,
                    config_file_sha256="c" * 64,
                    plan=plan,
                )
            out_of_range_unsigned = {
                **unsigned,
                "candidate_range": {
                    "start": 1024,
                    "count": 8,
                    "end_exclusive": 1032,
                },
                "worlds": [
                    {"candidate_index": index}
                    for index in range(1024, 1032)
                ],
            }
            out_of_range = {
                **out_of_range_unsigned,
                "shard_sha256": _sha256_json(out_of_range_unsigned),
            }
            with self.assertRaises(UtilizationFeasibilityError):
                _validate_shard(
                    out_of_range,
                    config_file_sha256="c" * 64,
                    plan=plan,
                )

    def test_cli_and_writer_boundaries(self) -> None:
        module = __import__("src.spark_strong_k4_utilization_feasibility", fromlist=["unused"])
        source = inspect.getsource(module)
        for name in module.__all__:
            self.assertTrue(hasattr(module, name), name)
        self.assertNotIn("scan_candidate_world", module.__all__)
        self.assertNotIn("materialize_private_candidate_world", module.__all__)
        self.assertNotIn("--execute", source)
        self.assertNotIn("load_provider_credentials", source)
        self.assertNotIn("OpenAICompatibleGenerator", source)
        with mock.patch.object(
            module,
            "generate_spark_world",
            side_effect=AssertionError("target materialized without authorization"),
        ) as generate:
            with self.assertRaisesRegex(
                UtilizationFeasibilityError,
                "reviewed-plan authorization",
            ):
                module._materialize_private_candidate_world(
                    _read(CONFIG_PATH),
                    1,
                    authorization=None,
                )
        generate.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            _emit_json_exclusive_0600({"ok": True}, output)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(UtilizationFeasibilityError, "overwrite"):
                _emit_json_exclusive_0600({"ok": False}, output)


if __name__ == "__main__":
    unittest.main()
