from __future__ import annotations

import copy
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import dsl, spark_lineage
from src import spark_strong_k4_utilization_feasibility as feasibility
from src import spark_strong_k4_utilization_primary_benchmark as benchmark
from src.provenance import PROJECT_ROOT


CONFIG_PATH = (
    PROJECT_ROOT
    / "configs"
    / "spark-strong-k4-utilization-primary-benchmark-v2.json"
)


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("fixture is not an object")
    return value


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _action(raw: int, label: str) -> dict[str, object]:
    frame = raw % 5
    if frame == 0:
        action = {
            "operation": "replace",
            "path": [1, 1] if raw < 5 else [1, 2],
            "binary_operator": None,
            "motif_side": None,
        }
    else:
        operator, side = (
            ("add", "right"),
            ("sub", "right"),
            ("sub", "left"),
            ("mul", "right"),
        )[frame - 1]
        action = {
            "operation": "wrap_binary",
            "path": [1, 1] if raw < 5 else [1, 2],
            "binary_operator": operator,
            "motif_side": side,
        }
    return {
        "raw_action_index": raw,
        "action": action,
        "child_behavior_hash": _digest(f"child-{label}"),
        "full_pool_counterfactual_bundle_sha256": _digest(f"bundle-{label}"),
    }


def _motif_pairs() -> dict[str, tuple[dict[str, object], dict[str, object]]]:
    grouped: dict[tuple[str, tuple[int, ...]], list[dict[str, object]]] = defaultdict(list)
    for raw in feasibility.enumerate_full_motif_library():
        row = dict(raw)
        grouped[(str(row["stratum"]), tuple(row["complexity_bucket"]))].append(row)
    result: dict[str, tuple[dict[str, object], dict[str, object]]] = {}
    for stratum in spark_lineage.MOTIF_STRATA:
        for (candidate_stratum, _), rows in grouped.items():
            if candidate_stratum != stratum:
                continue
            for index, left in enumerate(rows):
                for right in rows[index + 1 :]:
                    if left["motif_behavior_hash"] != right["motif_behavior_hash"]:
                        result[stratum] = (left, right)
                        break
                if stratum in result:
                    break
            if stratum in result:
                break
    if set(result) != set(spark_lineage.MOTIF_STRATA):
        raise AssertionError("cannot build motif-pair fixture")
    return result


def _strict_pair(stratum: str, ordinal: int) -> dict[str, object]:
    left, right = _motif_pairs()[stratum]
    raw_a = ordinal % 5
    raw_b = 5 + ordinal % 5
    return {
        "tier_id": feasibility.STRICT_TIER,
        "stratum": stratum,
        "complexity_bucket": list(left["complexity_bucket"]),
        "context_a_motif_id": left["motif_id"],
        "context_b_motif_id": right["motif_id"],
        "context_a_motif_behavior_hash": left["motif_behavior_hash"],
        "context_b_motif_behavior_hash": right["motif_behavior_hash"],
        "k2_opportunity_count": 2,
        "context_a_correct_raw_action_indices": [raw_a],
        "context_b_correct_raw_action_indices": [raw_b],
        "correct_raw_action_sets_disjoint": True,
        "context_a_correct_actions": [_action(raw_a, f"{ordinal}-a")],
        "context_b_correct_actions": [_action(raw_b, f"{ordinal}-b")],
    }


def _synthetic_inputs(
    directory: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    config = copy.deepcopy(_read(CONFIG_PATH))
    candidates = [
        {"candidate_index": index, "world_seed": 10_000 + index}
        for index in range(1024)
    ]
    feasibility_plan: dict[str, object] = {"candidates": candidates}
    files: list[dict[str, object]] = []
    total_size = 0
    for shard_index in range(128):
        start = shard_index * 8
        worlds = []
        for candidate_index in range(start, start + 8):
            strict_rows = []
            if candidate_index < 24:
                stratum = spark_lineage.MOTIF_STRATA[candidate_index // 6]
                strict_rows = [_strict_pair(stratum, candidate_index)]
            worlds.append(
                {
                    "candidate_index": candidate_index,
                    "parent_canonical_hash": dsl.canonical_hash(
                        dsl.parse_sexpr(
                            "(ite (eq (var x1) (const 0)) (const 1) (const 0))"
                        )
                    ),
                    "pair_candidates": {feasibility.STRICT_TIER: strict_rows},
                }
            )
        shard_sha = _digest(f"synthetic-shard-{shard_index}")
        shard = {
            "candidate_range": {
                "start": start,
                "count": 8,
                "end_exclusive": start + 8,
            },
            "worlds": worlds,
            "shard_sha256": shard_sha,
        }
        payload = benchmark._rendered_json_bytes(shard)
        relative = Path("private") / f"shard-{shard_index:03d}.json"
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        metadata = {
            "candidate_range": shard["candidate_range"],
            "relative_path": relative.as_posix(),
            "file_sha256": hashlib.sha256(payload).hexdigest(),
            "shard_sha256": shard_sha,
            "size_bytes": len(payload),
        }
        files.append(metadata)
        total_size += len(payload)
    files_sha = benchmark._sha256_json(files)
    config["upstream_geometry"]["shard_total_size_bytes"] = total_size  # type: ignore[index]
    config["upstream_geometry"]["shard_files_manifest_sha256"] = files_sha  # type: ignore[index]
    safe_manifest = {
        "artifacts": {
            "shards_private": {
                "files": files,
                "files_manifest_sha256": files_sha,
                "count": 128,
                "total_size_bytes": total_size,
            }
        }
    }
    feasibility_config: dict[str, object] = {}
    return config, safe_manifest, feasibility_config, feasibility_plan


def _synthetic_validator(
    shard: dict[str, object], **_: object
) -> tuple[int, int, list[dict[str, object]]]:
    candidate_range = shard["candidate_range"]
    assert isinstance(candidate_range, dict)
    worlds = shard["worlds"]
    assert isinstance(worlds, list)
    return (
        int(candidate_range["start"]),
        int(candidate_range["end_exclusive"]),
        worlds,
    )


def _synthetic_public_features(
    _world: object, motif_id: str, _lineages: object
) -> list[dict[str, object]]:
    return [
        {
            "raw_action_index": raw,
            "public_features": {
                "K1_supported": raw in {0, 5},
                "full_domain_positive_count": raw + 1 if raw in {0, 5} else None,
                "node_count": raw + 2 if raw in {0, 5} else None,
                "child_canonical_hash": (
                    _digest(f"public-child-{motif_id}-{raw}")
                    if raw in {0, 5}
                    else None
                ),
                "child_behavior_hash": (
                    _digest(f"public-behavior-{motif_id}-{raw}")
                    if raw in {0, 5}
                    else None
                ),
                "parent_behavior_novelty_count": raw if raw in {0, 5} else None,
                "child_behavior_is_constant": False if raw in {0, 5} else None,
            },
        }
        for raw in range(10)
    ]


def _synthetic_prompt_context(
    world_seed: int, expected_parent_hash: str | None = None
) -> tuple[object, dict[str, object]]:
    parent = "(ite (eq (var x1) (const 0)) (const 1) (const 0))"
    if expected_parent_hash != dsl.canonical_hash(dsl.parse_sexpr(parent)):
        raise AssertionError("selected shard parent was not bound into target-free replay")
    points = [
        [
            index % 5 - 2,
            (index // 5) % 5 - 2,
            (index * 2 + world_seed % 5) % 5 - 2,
        ]
        for index in range(12)
    ]
    return object(), {
        "D0": [
            {"point": point, "label": int(sum(point) == 0)} for point in points
        ],
        "parent": parent,
        "old_subtrees": {"LEFT": "x1", "RIGHT": "0"},
    }


class ConfigAndPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _read(CONFIG_PATH)
        cls.config_sha = hashlib.sha256(CONFIG_PATH.read_bytes()).hexdigest()

    def test_v2_labels_are_explicit_and_confirmatory_role_is_rejected(self) -> None:
        benchmark.validate_config(self.config)
        self.assertEqual(
            self.config["primary_route"]["role"],  # type: ignore[index]
            benchmark.MODEL_RESPONSE_LAYER_LABEL,
        )
        tampered = copy.deepcopy(self.config)
        tampered["primary_route"]["role"] = "confirmatory_primary"  # type: ignore[index]
        with self.assertRaises(benchmark.PrimaryBenchmarkError):
            benchmark.validate_config(tampered)

        tampered = copy.deepcopy(self.config)
        tampered["evidence_labels"]["significant_primary_interpretation"] = (  # type: ignore[index]
            "establishes model-general scientific discovery"
        )
        with self.assertRaises(benchmark.PrimaryBenchmarkError):
            benchmark.validate_config(tampered)

        for section, key, value in (
            ("primary_route", "request_model", "wrong-model"),
            ("analysis_binding", "primary_test", "posthoc test"),
            ("analysis_binding", "joint_observable_outcome_arm_exchangeability_required", False),
        ):
            with self.subTest(section=section, key=key):
                tampered = copy.deepcopy(self.config)
                tampered[section][key] = value  # type: ignore[index]
                with self.assertRaises(benchmark.PrimaryBenchmarkError):
                    benchmark.validate_config(tampered)

    def test_plan_is_target_free_and_freezes_24_slots(self) -> None:
        with mock.patch.object(
            benchmark,
            "_stream_validated_strict_worlds",
            side_effect=AssertionError("private shard opened while planning"),
        ) as stream:
            plan = benchmark.build_construction_plan(
                self.config,
                config_file_sha256=self.config_sha,
                source_manifest_sha256="a" * 64,
                source_freeze_git_head="b" * 40,
            )
        stream.assert_not_called()
        self.assertEqual(len(plan["pair_slots"]), 24)
        self.assertFalse(plan["private_shards_read"])
        self.assertEqual(plan["provider_calls_made"], 0)
        counts = {raw: [0] * 10 for raw in range(10)}
        for slot in plan["pair_slots"]:
            for position, raw in enumerate(slot["action_order"]):
                counts[raw][position] += 1
        self.assertTrue(all(set(row).issubset({2, 3}) for row in counts.values()))

        for field, value in (
            ("evidence_scope", "broader_claim"),
            ("upstream_bindings", {}),
            ("cohort_contract", {"world_count": 16}),
            ("primary_route", {"request_model": "wrong-model"}),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(plan)
                tampered[field] = value
                unsigned = {
                    key: item for key, item in tampered.items() if key != "plan_sha256"
                }
                tampered["plan_sha256"] = benchmark._sha256_json(unsigned)
                with self.assertRaises(benchmark.PrimaryBenchmarkError):
                    benchmark.validate_construction_plan(
                        self.config,
                        tampered,
                        config_file_sha256=self.config_sha,
                        require_current_source=False,
                    )

    def test_reviewed_plan_hashes_gate_private_stream(self) -> None:
        plan = benchmark.build_construction_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256="a" * 64,
            source_freeze_git_head="b" * 40,
        )
        with tempfile.TemporaryDirectory() as temporary:
            plan_path = Path(temporary) / "plan.json"
            plan_path.write_bytes(benchmark._rendered_json_bytes(plan))
            with mock.patch.object(
                benchmark,
                "_stream_validated_strict_worlds",
                side_effect=AssertionError("private shard opened before review"),
            ) as stream:
                with self.assertRaisesRegex(
                    benchmark.PrimaryBenchmarkError, "reviewed semantic"
                ):
                    benchmark.construct_benchmark(
                        self.config,
                        plan,
                        config_file_sha256=self.config_sha,
                        reviewed_plan_sha256="0" * 64,
                        reviewed_plan_file_sha256=hashlib.sha256(
                            plan_path.read_bytes()
                        ).hexdigest(),
                        plan_path=plan_path,
                        require_current_source=False,
                    )
            stream.assert_not_called()

            with mock.patch.object(
                benchmark,
                "_stream_validated_strict_worlds",
                side_effect=AssertionError("private shard opened before review"),
            ) as stream:
                with self.assertRaisesRegex(
                    benchmark.PrimaryBenchmarkError, "reviewed plan file"
                ):
                    benchmark.construct_benchmark(
                        self.config,
                        plan,
                        config_file_sha256=self.config_sha,
                        reviewed_plan_sha256=plan["plan_sha256"],
                        reviewed_plan_file_sha256="0" * 64,
                        plan_path=plan_path,
                        require_current_source=False,
                    )
            stream.assert_not_called()

    def test_current_source_requires_matching_head_and_clean_tree(self) -> None:
        plan = benchmark.build_construction_plan(
            self.config,
            config_file_sha256=self.config_sha,
            source_manifest_sha256="a" * 64,
            source_freeze_git_head="b" * 40,
        )
        current = {
            "source_manifest_sha256": "a" * 64,
            "environment": {"git_head": "c" * 40},
        }
        with mock.patch.object(benchmark, "source_manifest", return_value=current):
            with self.assertRaisesRegex(benchmark.PrimaryBenchmarkError, "Git head"):
                benchmark.validate_construction_plan(
                    self.config,
                    plan,
                    config_file_sha256=self.config_sha,
                    require_current_source=True,
                )

        current["environment"]["git_head"] = "b" * 40  # type: ignore[index]
        dirty_status = mock.Mock(returncode=0, stdout=" M src/example.py\n")
        with mock.patch.object(benchmark.subprocess, "run", return_value=dirty_status):
            with self.assertRaisesRegex(benchmark.PrimaryBenchmarkError, "clean"):
                benchmark._assert_clean_source_freeze(PROJECT_ROOT, current)

    def test_target_free_parent_replay_is_hash_bound(self) -> None:
        world, _ = benchmark._target_free_prompt_context(12345)
        expected = dsl.canonical_hash(spark_lineage.select_parent(world))
        benchmark._target_free_prompt_context(12345, expected)
        with self.assertRaisesRegex(benchmark.PrimaryBenchmarkError, "parent replay"):
            benchmark._target_free_prompt_context(12345, "0" * 64)


class SyntheticConstructionTests(unittest.TestCase):
    def test_private_shard_raw_hash_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, safe, feasibility_config, feasibility_plan = _synthetic_inputs(root)
            first = safe["artifacts"]["shards_private"]["files"][0]
            shard_path = root / first["relative_path"]
            shard_path.write_bytes(shard_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(
                benchmark.PrimaryBenchmarkError, "private shard 000 bytes"
            ):
                benchmark._stream_validated_strict_worlds(
                    config,
                    safe,
                    feasibility_config,
                    feasibility_plan,
                    project_root=root,
                    shard_validator=_synthetic_validator,
                )

    def test_all_128_shards_to_masked_48_task_bijection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, safe, feasibility_config, feasibility_plan = _synthetic_inputs(root)
            upstreams = {
                "safe_manifest": safe,
                "feasibility_config": feasibility_config,
                "feasibility_plan": feasibility_plan,
                "power_config": {},
                "power_plan": {},
                "power_result": {},
            }
            config_sha = benchmark._sha256_json(config)
            with (
                mock.patch.object(
                    benchmark, "CONFIG_CANONICAL_SHA256", config_sha
                ),
                mock.patch.object(
                    benchmark, "_load_tracked_upstreams", return_value=upstreams
                ),
                mock.patch.object(
                    benchmark,
                    "_target_free_prompt_context",
                    side_effect=_synthetic_prompt_context,
                ),
            ):
                plan = benchmark.build_construction_plan(
                    config,
                    config_file_sha256=config_sha,
                    source_manifest_sha256="a" * 64,
                    source_freeze_git_head="b" * 40,
                )
                plan_path = root / "plan.json"
                plan_path.write_bytes(benchmark._rendered_json_bytes(plan))
                public, private, result = benchmark.construct_benchmark(
                    config,
                    plan,
                    config_file_sha256=config_sha,
                    reviewed_plan_sha256=plan["plan_sha256"],
                    reviewed_plan_file_sha256=hashlib.sha256(
                        plan_path.read_bytes()
                    ).hexdigest(),
                    plan_path=plan_path,
                    project_root=root,
                    require_current_source=False,
                    shard_validator=_synthetic_validator,
                    public_feature_builder=_synthetic_public_features,
                )
            frozen_config = _read(CONFIG_PATH)
            self.assertEqual(public["task_count"], 48)
            self.assertEqual(private["pair_count"], 24)
            self.assertEqual(result["shard_validation"]["validated_shard_count"], 128)
            self.assertEqual(
                result["selection"]["counts_by_construction_stratum"],
                {stratum: 6 for stratum in spark_lineage.MOTIF_STRATA},
            )
            for artifact in (plan, public, private, result):
                self.assertEqual(
                    artifact["world_layer_label"],
                    "outcome_conditioned_development_only",
                )
                self.assertEqual(
                    artifact["model_response_layer_label"],
                    "preregistered_prospective_primary",
                )
            self.assertFalse(result["evidence"])
            self.assertFalse(result["independent_heldout_confirmation"])
            leaked_public = copy.deepcopy(public)
            leaked_public["target_seed"] = 123
            leaked_unsigned = {
                key: value
                for key, value in leaked_public.items()
                if key != "public_manifest_sha256"
            }
            leaked_public["public_manifest_sha256"] = benchmark._sha256_json(
                leaked_unsigned
            )
            with self.assertRaises(benchmark.PrimaryBenchmarkError):
                benchmark.validate_public_manifest(leaked_public)
            public_text = json.dumps(public["tasks"], sort_keys=True).lower()
            for forbidden in (
                "candidate_index",
                "world_seed",
                "motif_id",
                "endpoint_flags",
                "context_a",
                "context_b",
            ):
                self.assertNotIn(forbidden, public_text)
            benchmark.validate_private_key(
                private,
                public,
                config=frozen_config,
                plan=plan,
                target_free_context_builder=_synthetic_prompt_context,
            )
            benchmark.validate_construction_result(result, public, private)

            tampered_private = copy.deepcopy(private)
            tampered_private["analysis_binding"]["primary_alpha"]["denominator"] = 1
            private_unsigned = {
                key: value
                for key, value in tampered_private.items()
                if key != "private_key_sha256"
            }
            tampered_private["private_key_sha256"] = benchmark._sha256_json(
                private_unsigned
            )
            with self.assertRaises(benchmark.PrimaryBenchmarkError):
                benchmark.validate_private_key(
                    tampered_private,
                    public,
                    config=frozen_config,
                    plan=plan,
                    target_free_context_builder=_synthetic_prompt_context,
                )

            seed_tampered_private = copy.deepcopy(private)
            seed_tampered_private["pairs"][0]["world_binding"]["world_seed"] += 1
            seed_design_commitment = benchmark._sha256_json(
                {
                    "protocol_id": benchmark.PROTOCOL_ID,
                    "config_file_sha256": plan["config_file_sha256"],
                    "plan_sha256": plan["plan_sha256"],
                    "pairs": seed_tampered_private["pairs"],
                    "baseline_report": seed_tampered_private["baseline_report"],
                }
            )
            seed_tampered_public = copy.deepcopy(public)
            seed_tampered_public[
                "private_design_commitment_sha256"
            ] = seed_design_commitment
            public_unsigned = {
                key: value
                for key, value in seed_tampered_public.items()
                if key != "public_manifest_sha256"
            }
            seed_tampered_public[
                "public_manifest_sha256"
            ] = benchmark._sha256_json(public_unsigned)
            seed_tampered_private[
                "private_design_commitment_sha256"
            ] = seed_design_commitment
            seed_tampered_private["public_manifest_sha256"] = seed_tampered_public[
                "public_manifest_sha256"
            ]
            private_unsigned = {
                key: value
                for key, value in seed_tampered_private.items()
                if key != "private_key_sha256"
            }
            seed_tampered_private["private_key_sha256"] = benchmark._sha256_json(
                private_unsigned
            )
            with self.assertRaisesRegex(
                benchmark.PrimaryBenchmarkError, "world-seed replay"
            ):
                benchmark.validate_private_key(
                    seed_tampered_private,
                    seed_tampered_public,
                    config=frozen_config,
                    plan=plan,
                    target_free_context_builder=_synthetic_prompt_context,
                )

            tampered_result = copy.deepcopy(result)
            tampered_result["selection"][
                "selected_candidate_indices_in_pair_slot_order"
            ] = [999] * 24
            result_unsigned = {
                key: value
                for key, value in tampered_result.items()
                if key != "construction_result_sha256"
            }
            tampered_result["construction_result_sha256"] = benchmark._sha256_json(
                result_unsigned
            )
            with self.assertRaisesRegex(
                benchmark.PrimaryBenchmarkError, "selection summary"
            ):
                benchmark.validate_construction_result(
                    tampered_result, public, private
                )

            output = root / "output"
            paths = benchmark.write_benchmark_artifacts(
                public,
                private,
                result,
                output,
                config=frozen_config,
                plan=plan,
                target_free_context_builder=_synthetic_prompt_context,
            )
            self.assertEqual(set(paths), {"public", "private", "result"})
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in paths.values()))
            with self.assertRaisesRegex(benchmark.PrimaryBenchmarkError, "overwrite"):
                benchmark.write_benchmark_artifacts(
                    public,
                    private,
                    result,
                    output,
                    config=frozen_config,
                    plan=plan,
                    target_free_context_builder=_synthetic_prompt_context,
                )


if __name__ == "__main__":
    unittest.main()
