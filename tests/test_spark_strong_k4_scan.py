from __future__ import annotations

import copy
import hashlib
import io
import inspect
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.provenance import PROJECT_ROOT
from src.spark_lineage import MOTIF_STRATA
from src.spark_strong_k4_scan import (
    StrongK4ScanError,
    _cohort_diversity_report,
    _is_k2,
    _validate_shard,
    build_public_candidate_projection,
    build_target_free_scan_plan,
    classify_full_pool_controls,
    derive_candidate_seed_vector,
    derive_candidate_world_seed,
    derive_private_target_seed,
    deterministic_balanced_matching,
    main,
    materialize_private_candidate_world,
    merge_scan_shards,
    validate_config,
    validate_scan_plan,
    validate_seed_registry,
)


CONFIG_PATH = PROJECT_ROOT / "configs" / "spark-strong-k4-feasibility-v2.json"
REGISTRY_PATH = PROJECT_ROOT / "configs" / "development-seed-registry.json"
OPPORTUNITY_MAP_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-cross-model-matched-triad-v1-20260821"
    / "action-opportunity-map.json"
)


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("fixture is not an object")
    return value


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _control(behavior: str, exact: bool, n_t: int = 2) -> dict[str, object]:
    return {
        "child_behavior_hash": behavior * 64,
        "exact_identification": exact,
        "N_T": n_t,
    }


class StrongK4PredicateTests(unittest.TestCase):
    def test_truth_table_is_nested_and_requires_three_unique_failures(self) -> None:
        failures = [_control("a", False), _control("b", False), _control("c", False)]
        self.assertTrue(
            classify_full_pool_controls(k3=True, control_outcomes=failures)[
                "K4_full_pool"
            ]
        )
        self.assertFalse(
            classify_full_pool_controls(k3=False, control_outcomes=failures)[
                "K4_full_pool"
            ]
        )
        one_success = [*failures[:2], _control("c", True, 1)]
        self.assertFalse(
            classify_full_pool_controls(k3=True, control_outcomes=one_success)[
                "K4_full_pool"
            ]
        )
        self.assertFalse(
            classify_full_pool_controls(k3=True, control_outcomes=failures[:2])[
                "K4_full_pool"
            ]
        )

    def test_behavior_deduplication_and_conflict_fail_closed(self) -> None:
        consistent_duplicate = [
            _control("a", False),
            _control("a", False),
            _control("b", False),
            _control("c", False),
        ]
        result = classify_full_pool_controls(
            k3=True, control_outcomes=consistent_duplicate
        )
        self.assertTrue(result["K4_full_pool"])
        self.assertEqual(result["unique_control_behavior_count"], 3)

        conflict = [
            _control("a", False, 2),
            _control("a", True, 1),
            _control("b", False),
            _control("c", False),
        ]
        with self.assertRaisesRegex(StrongK4ScanError, "conflicting"):
            classify_full_pool_controls(k3=True, control_outcomes=conflict)

    def test_k2_reuses_four_round_mediated_gate(self) -> None:
        positive_step = SimpleNamespace(
            response=SimpleNamespace(is_match=False), N_after=1, N_before=2
        )
        result = SimpleNamespace(
            truth_retained=True,
            N_T=1,
            full_domain_recovered=True,
            steps=(positive_step,),
            rounds_completed=4,
        )
        self.assertTrue(_is_k2(result, child_direct_hit=False))
        self.assertFalse(_is_k2(result, child_direct_hit=True))
        result.rounds_completed = 5
        self.assertFalse(_is_k2(result, child_direct_hit=False))

    def test_existing_32_world_artifact_reduces_to_five_actions_two_slots(self) -> None:
        artifact = _read(OPPORTUNITY_MAP_PATH)
        worlds = artifact["landscape"]["worlds"]
        raw_hits = 0
        slot_hits = 0
        world_hits = 0
        for world in worlds:
            this_world = False
            for slot in world["slots"]:
                this_slot = False
                for action in slot["actions"]:
                    robustness = action.get("full_replacement_pool_robustness")
                    if not robustness:
                        continue
                    controls = [
                        {
                            "child_behavior_hash": row["child_behavior_hash"],
                            "exact_identification": row["reaches_endpoint"],
                            "N_T": row["N_T"],
                        }
                        for row in robustness["replacement_outcomes"]
                    ]
                    hit = classify_full_pool_controls(
                        k3=action["endpoint_flags"]["K3"],
                        control_outcomes=controls,
                    )["K4_full_pool"]
                    raw_hits += bool(hit)
                    this_slot |= bool(hit)
                    this_world |= bool(hit)
                slot_hits += this_slot
            world_hits += this_world
        self.assertEqual((raw_hits, slot_hits, world_hits), (5, 2, 2))


class StrongK4PlanAndSeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _read(CONFIG_PATH)
        cls.registry = _read(REGISTRY_PATH)
        cls.config_sha = _digest(CONFIG_PATH)
        cls.registry_sha = _digest(REGISTRY_PATH)

    def test_frozen_seed_vector_and_registry_suffix(self) -> None:
        validate_config(self.config)
        seeds = derive_candidate_seed_vector(self.config)
        self.assertEqual(
            seeds[:5],
            (
                735700605445006337,
                7938803063462999235,
                682719666839328610,
                129701998817161371,
                8794655317188480765,
            ),
        )
        self.assertEqual(
            derive_candidate_world_seed(
                "spark-strong-k4-feasibility-v2:world-seed", 0
            ),
            seeds[0],
        )
        self.assertEqual(len(seeds), len(set(seeds)))
        self.assertEqual(
            hashlib.sha256(
                json.dumps(seeds, separators=(",", ":")).encode("ascii")
            ).hexdigest(),
            "05ddba77ac4e5bded52446f0168334b4ef6eaeabe8dbc4bb9baa1928c6becc42",
        )
        audit = validate_seed_registry(self.config, self.registry)
        self.assertTrue(audit["registry_suffix_exact"])
        self.assertEqual(audit["historical_collision_count"], 0)

    def _plan(self) -> dict[str, object]:
        return build_target_free_scan_plan(
            self.config,
            self.registry,
            config_file_sha256=self.config_sha,
            registry_file_sha256=self.registry_sha,
            source_manifest_sha256="f" * 64,
        )

    def test_plan_is_target_free_and_binds_original_candidate_indices(self) -> None:
        with (
            mock.patch(
                "src.spark_strong_k4_scan.generate_spark_world",
                side_effect=AssertionError("target was materialized"),
            ),
            mock.patch(
                "src.spark_strong_k4_scan.SparkCompressor",
                side_effect=AssertionError("compressor ran"),
            ),
        ):
            plan = self._plan()
        self.assertFalse(plan["target_materialized"])
        self.assertFalse(plan["compressor_run"])
        self.assertEqual(plan["provider_calls_made"], 0)
        candidates = plan["candidates"]
        self.assertEqual(len(candidates), 1024)
        for index in (0, 17, 1023):
            candidate = candidates[index]
            self.assertEqual(candidate["candidate_index"], index)
            self.assertTrue(
                all(slot["candidate_index"] == index for slot in candidate["slots"])
            )
            expected = tuple(
                MOTIF_STRATA[(index * 3 + factual_index) % 4]
                for factual_index in range(3)
            )
            self.assertEqual(
                tuple(slot["motif_stratum"] for slot in candidate["slots"]),
                expected,
            )

    def test_plan_tamper_is_rejected(self) -> None:
        plan = self._plan()
        tampered = copy.deepcopy(plan)
        tampered["candidates"][9]["world_seed"] += 1
        with self.assertRaises(StrongK4ScanError):
            validate_scan_plan(
                self.config,
                tampered,
                config_file_sha256=self.config_sha,
                require_current_source=False,
            )

    def test_private_target_uses_full_digest_rule_and_one_world_draw(self) -> None:
        # A deliberately non-protocol namespace/seed tests the seam without
        # deriving any reserved v2 target seed before the plan barrier.
        world_seed = 123
        namespace = "strong-k4-unit-test-only"
        config = {
            "private_target_and_public_motif_namespaces": {
                "target_seed_namespace": namespace
            }
        }
        expected = int.from_bytes(
            hashlib.sha256(
                f"{namespace}:target:{world_seed}".encode("ascii")
            ).digest(),
            "big",
        )
        self.assertEqual(
            derive_private_target_seed(config, world_seed), expected
        )
        sentinel = object()
        with mock.patch(
            "src.spark_strong_k4_scan.generate_spark_world",
            return_value=sentinel,
        ) as generate:
            self.assertIs(
                materialize_private_candidate_world(config, world_seed),
                sentinel,
            )
        generate.assert_called_once_with(world_seed, target_seed=expected)

    def test_public_projection_excludes_every_private_field(self) -> None:
        candidate = self._plan()["candidates"][0]
        dummy_world = {
            "world_index": 0,
            "world_seed": candidate["world_seed"],
            "D0": [{"point": [0, 0, 0], "label": 0}],
            "parent": "(const 0)",
            "parent_canonical_hash": "a" * 64,
            "allowed_paths": [],
        }
        with (
            mock.patch(
                "src.spark_strong_k4_scan.spark_closure._target_free_public_world_entry",
                return_value=dummy_world,
            ),
            mock.patch(
                "src.spark_strong_k4_scan.generate_spark_world",
                side_effect=AssertionError("target was materialized"),
            ),
        ):
            projection = build_public_candidate_projection(candidate)
        rendered = json.dumps(projection, sort_keys=True)
        for forbidden in self.config["artifact_contract"][
            "future_public_projection_excludes"
        ]:
            self.assertNotIn(f'"{forbidden}"', rendered)


class StrongK4MergeAndInterfaceTests(unittest.TestCase):
    def test_matching_uses_each_world_at_most_once(self) -> None:
        eligible = {
            index: tuple(MOTIF_STRATA)
            for index in range(32)
        }
        result = deterministic_balanced_matching(eligible)
        self.assertEqual(result["classification"], "full_32_balanced_feasible")
        assignments = result["assignments"]
        selected = [row["candidate_index"] for row in assignments]
        self.assertEqual(len(selected), 32)
        self.assertEqual(len(selected), len(set(selected)))
        self.assertEqual(
            result["counts_by_construction_stratum"],
            {stratum: 8 for stratum in MOTIF_STRATA},
        )

    def test_matching_falls_back_to_six_per_stratum(self) -> None:
        eligible = {
            stratum_index * 7 + offset: (stratum,)
            for stratum_index, stratum in enumerate(MOTIF_STRATA)
            for offset in range(7)
        }
        result = deterministic_balanced_matching(eligible)
        self.assertEqual(result["classification"], "reduced_24_balanced_feasible")
        self.assertEqual(result["matched_world_count"], 24)
        self.assertEqual(
            result["capacity_audit"]["maximum_joint_balanced_q_up_to_target"],
            7,
        )
        self.assertEqual(result["capacity_audit"]["joint_target_q_deficit"], 1)

    def test_matching_uses_frozen_lexicographic_joint_tie_break(self) -> None:
        # Marginally, world 0 can fill either first or second stratum.  The
        # frozen concatenated-vector order prefers A=0,B=2 over A=1,B=0.
        result = deterministic_balanced_matching(
            {
                0: ("A", "B"),
                1: ("A",),
                2: ("B",),
            },
            strata=("A", "B"),
            target_per_stratum=1,
            fallback_per_stratum=1,
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["lexicographic_assignment_vector"], [0, 2])
        self.assertEqual(
            {
                row["construction_stratum"]: row["candidate_index"]
                for row in result["assignments"]
            },
            {"A": 0, "B": 2},
        )

        symmetric = deterministic_balanced_matching(
            {0: ("A", "B"), 1: ("A", "B")},
            strata=("A", "B"),
            target_per_stratum=1,
            fallback_per_stratum=1,
        )
        self.assertEqual(symmetric["lexicographic_assignment_vector"], [0, 1])

    def test_joint_matching_rejects_hall_overlap_despite_marginal_counts(self) -> None:
        # A and B each have one eligible world marginally, but it is the same
        # world and world capacity is one.
        result = deterministic_balanced_matching(
            {0: ("A", "B"), 1: ("C",), 2: ("D",)},
            strata=("A", "B", "C", "D"),
            target_per_stratum=1,
            fallback_per_stratum=1,
        )
        self.assertFalse(result["complete"])
        self.assertEqual(
            result["classification"],
            "balanced_strong_K4_benchmark_not_feasible_under_cap",
        )
        self.assertEqual(
            result["capacity_audit"]["marginal_by_stratum"]["A"],
            {
                "eligible_world_capacity": 1,
                "target_q": 1,
                "target_q_deficit": 0,
                "fallback_q": 1,
                "fallback_q_deficit": 0,
            },
        )
        self.assertEqual(
            result["capacity_audit"]["maximum_joint_balanced_q_up_to_target"],
            0,
        )
        self.assertEqual(result["capacity_audit"]["joint_target_world_deficit"], 4)

    def test_cohort_reports_repetition_and_low_semantic_diversity(self) -> None:
        child = "c" * 64
        target = "t" * 64
        worlds = []
        assignments = []
        for index, stratum in enumerate(MOTIF_STRATA):
            action = {
                "child_behavior_hash": child,
                "endpoint_flags": {"K4_full_pool": True},
                "full_pool_counterfactual_bundle": {
                    "frame": {
                        "operation": "replace",
                        "path": [1, 1],
                        "motif_stratum": stratum,
                    }
                },
            }
            worlds.append(
                {
                    "candidate_index": index,
                    "private_outcome": {
                        "target_canonical_hash": target,
                        "slots": [
                            {
                                "slot_id": f"slot-{index}",
                                "motif_id": "repeated-motif",
                                "motif_stratum": stratum,
                                "actions": [action],
                            }
                        ],
                    },
                }
            )
            assignments.append(
                {
                    "candidate_index": index,
                    "construction_stratum": stratum,
                }
            )
        report = _cohort_diversity_report(worlds, assignments)
        self.assertTrue(report["low_semantic_diversity"])
        self.assertEqual(
            report["global_qualifying_unique_child_behavior_count"], 1
        )
        self.assertEqual(
            report["selected_target_canonical_hash_diversity"]
            ["unique_identity_count"],
            1,
        )
        self.assertEqual(
            report["selected_child_behavior_diversity"]
            ["repeated_observation_excess_count"],
            3,
        )

    def test_merge_range_gap_and_overlap_fail_before_selection(self) -> None:
        config = {"candidate_stream": {"stage_world_count": 64}}
        plan = {"plan_sha256": "p", "source_manifest_sha256": "s"}
        dummy_shards = [{}, {}]
        for ranges, word in (
            ([(0, 64, []), (65, 129, [])], "gap"),
            ([(0, 64, []), (63, 127, [])], "overlap"),
        ):
            with (
                mock.patch(
                    "src.spark_strong_k4_scan.validate_scan_plan",
                    return_value=tuple(None for _ in range(1024)),
                ),
                mock.patch(
                    "src.spark_strong_k4_scan._validate_shard",
                    side_effect=ranges,
                ),
                self.assertRaisesRegex(StrongK4ScanError, word),
            ):
                merge_scan_shards(
                    config,
                    plan,
                    dummy_shards,
                    config_file_sha256="c" * 64,
                    require_current_source=False,
                )

    def test_shard_byte_level_content_tamper_fails_digest(self) -> None:
        shard = {
            "schema_version": 1,
            "kind": "spark-strong-k4-feasibility-scan-shard",
            "protocol_id": "spark-strong-k4-feasibility-v2",
            "config_file_sha256": "c" * 64,
            "plan_sha256": "p",
            "source_manifest_sha256": "s",
            "candidate_range": {"start": 0, "count": 0, "end_exclusive": 0},
            "worlds": [],
            "aggregate": {},
            "model_outputs_read": False,
            "provider_calls_made": 0,
            "outcome_conditioned_benchmark_construction": True,
            "shard_sha256": "0" * 64,
        }
        with self.assertRaisesRegex(StrongK4ScanError, "tampered"):
            _validate_shard(
                shard,
                config_file_sha256="c" * 64,
                plan={"plan_sha256": "p", "source_manifest_sha256": "s"},
            )

    def test_cli_has_no_execute_or_model_api_surface(self) -> None:
        source = inspect.getsource(__import__(
            "src.spark_strong_k4_scan", fromlist=["unused"]
        ))
        self.assertNotIn("--execute", source)
        self.assertNotIn("load_provider_credentials", source)
        self.assertNotIn("OpenAICompatibleGenerator", source)
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            main(["scan", "--help"])

    def test_exclusive_writer_refuses_overwrite_and_uses_0600(self) -> None:
        config = _read(CONFIG_PATH)
        registry = _read(REGISTRY_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            registry_path = root / "registry.json"
            output = root / "plan.json"
            config_path.write_bytes(CONFIG_PATH.read_bytes())
            registry_path.write_bytes(REGISTRY_PATH.read_bytes())
            with mock.patch(
                "src.spark_strong_k4_scan._current_source_manifest_sha256",
                return_value="f" * 64,
            ):
                self.assertEqual(
                    main(
                        [
                            "plan",
                            "--config",
                            str(config_path),
                            "--registry",
                            str(registry_path),
                            "--output",
                            str(output),
                        ]
                    ),
                    0,
                )
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(StrongK4ScanError, "overwrite"):
                    main(
                        [
                            "plan",
                            "--config",
                            str(config_path),
                            "--registry",
                            str(registry_path),
                            "--output",
                            str(output),
                        ]
                    )


if __name__ == "__main__":
    unittest.main()
