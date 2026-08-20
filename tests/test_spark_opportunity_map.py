from __future__ import annotations

import copy
import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import dsl
from src.provenance import PROJECT_ROOT
from src.spark_closure import ParsedAction, parse_action
from src.spark_cross_model import CROSS_MODEL_ARM_IDS
from src.spark_lineage import (
    EditAction,
    apply_edit,
    enumerate_reachable_children,
    motif_by_id,
)
from src.spark_opportunity_map import (
    OpportunityMapError,
    build_action_opportunity_landscape,
    build_action_opportunity_map,
    enumerate_raw_slot_actions,
    main,
    overlay_sealed_model_actions,
)
from src.spark_world import generate_spark_world


ARTIFACT_DIRECTORY = (
    PROJECT_ROOT / "artifacts" / "spark-cross-model-matched-triad-v1-20260821"
)
PLAN_PATH = ARTIFACT_DIRECTORY / "plan.json"
GENERATIONS_PATH = ARTIFACT_DIRECTORY / "generations.json"
ANALYSIS_PATH = ARTIFACT_DIRECTORY / "analysis.json"


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"test fixture {path} is not an object")
    return value


def _wire_action(action: object) -> str:
    operation = getattr(action, "operation")
    path = " ".join(str(value) for value in getattr(action, "path"))
    if operation == "replace":
        return f"(edit replace {path})"
    return (
        f"(edit wrap_binary {path} "
        f"{getattr(action, 'binary_operator')} {getattr(action, 'motif_side')})"
    )


def _parsed_for_edit_action(action: object) -> ParsedAction:
    return ParsedAction(
        getattr(action, "operation"),
        path=getattr(action, "path"),
        binary_operator=getattr(action, "binary_operator"),
        motif_side=getattr(action, "motif_side"),
    )


def _edit_action_from_row(
    row: dict[str, object], *, motif_id: str
) -> EditAction:
    action = row["action"]
    if not isinstance(action, dict):
        raise AssertionError("landscape action is not an object")
    path = action.get("path")
    if not isinstance(path, list) or len(path) != 2:
        raise AssertionError("landscape action path is malformed")
    return EditAction(
        operation=str(action["operation"]),  # type: ignore[arg-type]
        path=(int(path[0]), int(path[1])),
        expected_old_subtree_hash=str(row["expected_old_subtree_hash"]),
        motif_id=motif_id,
        binary_operator=action.get("binary_operator"),  # type: ignore[arg-type]
        motif_side=action.get("motif_side"),  # type: ignore[arg-type]
    )


def _flatten_landscape_slots(landscape: dict[str, object]) -> list[dict[str, object]]:
    worlds = landscape["worlds"]
    if not isinstance(worlds, list):
        raise AssertionError("landscape worlds are malformed")
    return [
        slot
        for world in worlds
        for slot in world["slots"]
        if isinstance(slot, dict)
    ]


def _lineage_control_signature(records: object, motif_id: str) -> tuple[object, ...]:
    matching = [record for record in records if record.motif_id == motif_id]
    return tuple(
        sorted(
            (
                record.action.operation,
                record.action.path,
                record.action.binary_operator,
                record.action.motif_side,
                record.action.expected_old_subtree_hash,
                record.child_canonical_hash,
                record.child_behavior_hash,
                tuple(
                    (
                        replacement.motif_id,
                        replacement.child_canonical_hash,
                        replacement.child_behavior_hash,
                    )
                    for replacement in record.matched_replacements
                ),
            )
            for record in matching
        )
    )


class SparkOpportunityMapUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = _read_json(PLAN_PATH)
        cls.world_seed = int(cls.plan["world_seeds"][0])  # type: ignore[index]
        cls.motif_id = str(cls.plan["slots"][0]["motif_id"])  # type: ignore[index]

    def test_landscape_has_no_generation_or_analysis_input(self) -> None:
        parameters = inspect.signature(
            build_action_opportunity_landscape
        ).parameters
        self.assertEqual(tuple(parameters), ("plan",))

    def test_prompt_action_menu_has_exactly_ten_grammar_roundtrips(self) -> None:
        world = generate_spark_world(self.world_seed, target_seed=0)
        actions = enumerate_raw_slot_actions(world, self.motif_id)
        self.assertEqual(len(actions), 10)
        self.assertEqual(len(set(actions)), 10)

        expected_frames = {
            (path, operation, operator, side)
            for path in ((1, 1), (1, 2))
            for operation, operator, side in (
                ("replace", None, None),
                ("wrap_binary", "add", "right"),
                ("wrap_binary", "sub", "right"),
                ("wrap_binary", "sub", "left"),
                ("wrap_binary", "mul", "right"),
            )
        }
        observed_frames = {
            (
                action.path,
                action.operation,
                action.binary_operator,
                action.motif_side,
            )
            for action in actions
        }
        self.assertEqual(observed_frames, expected_frames)
        for action in actions:
            with self.subTest(action=_wire_action(action)):
                self.assertEqual(
                    parse_action(_wire_action(action)),
                    _parsed_for_edit_action(action),
                )

    def test_target_seed_does_not_change_action_or_control_universe(self) -> None:
        first = generate_spark_world(self.world_seed, target_seed=0)
        second = generate_spark_world(self.world_seed, target_seed=1)
        self.assertNotEqual(first.target_index, second.target_index)

        raw_first = enumerate_raw_slot_actions(first, self.motif_id)
        raw_second = enumerate_raw_slot_actions(second, self.motif_id)
        self.assertEqual(
            tuple(action.action_hash for action in raw_first),
            tuple(action.action_hash for action in raw_second),
        )
        first_signature = _lineage_control_signature(
            enumerate_reachable_children(first), self.motif_id
        )
        second_signature = _lineage_control_signature(
            enumerate_reachable_children(second), self.motif_id
        )
        self.assertTrue(first_signature)
        self.assertEqual(first_signature, second_signature)

    def test_byte_tamper_fails_closed_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            generations_path = root / "generations.json"
            analysis_path = root / "analysis.json"
            output_path = root / "opportunity.json"
            plan_path.write_bytes(PLAN_PATH.read_bytes() + b"\n")
            generations_path.write_bytes(GENERATIONS_PATH.read_bytes())
            analysis_path.write_bytes(ANALYSIS_PATH.read_bytes())

            with (
                mock.patch(
                    "src.spark_opportunity_map.build_action_opportunity_map",
                    side_effect=AssertionError("opportunity core was entered"),
                ) as build,
                self.assertRaises(OpportunityMapError),
            ):
                main(
                    [
                        "--plan",
                        str(plan_path),
                        "--generations",
                        str(generations_path),
                        "--analysis",
                        str(analysis_path),
                        "--output",
                        str(output_path),
                    ]
                )
            build.assert_not_called()
            self.assertFalse(output_path.exists())


class SparkOpportunityMapFormalIntegrationTests(unittest.TestCase):
    """Single sealed 960-action integration; all assertions reuse one build."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = _read_json(PLAN_PATH)
        cls.bundle = _read_json(GENERATIONS_PATH)
        cls.analysis = _read_json(ANALYSIS_PATH)
        cls.landscape = build_action_opportunity_landscape(cls.plan)
        cls.overlay = overlay_sealed_model_actions(
            cls.plan,
            cls.bundle,
            cls.analysis,
            cls.landscape,
        )
        cls.slots = _flatten_landscape_slots(cls.landscape)

    def test_landscape_is_exactly_one_offline_32_by_3_by_10_census(self) -> None:
        worlds = self.landscape["worlds"]
        self.assertEqual(len(worlds), 32)
        self.assertEqual(len(self.slots), 96)
        self.assertEqual(
            sum(len(slot["actions"]) for slot in self.slots),
            960,
        )
        self.assertEqual(
            [world["world_seed"] for world in worlds],
            self.plan["world_seeds"],
        )
        self.assertEqual(
            [slot["slot_id"] for slot in self.slots],
            [slot["slot_id"] for slot in self.plan["slots"]],
        )
        self.assertIs(self.landscape["model_outputs_read"], False)
        self.assertEqual(self.landscape["provider_calls_made"], 0)
        self.assertIs(self.landscape["formal_analysis_mutated"], False)
        aggregate = self.landscape["aggregate"]
        self.assertEqual(aggregate["world_count"], 32)
        self.assertEqual(aggregate["slot_count"], 96)
        self.assertEqual(aggregate["raw_syntactic_action_count"], 960)

        for slot in self.slots:
            actions = slot["actions"]
            self.assertEqual(len(actions), 10)
            self.assertEqual(
                [action["raw_action_index"] for action in actions],
                list(range(10)),
            )
            self.assertEqual(
                len(
                    {
                        json.dumps(action["action"], sort_keys=True)
                        for action in actions
                    }
                ),
                10,
            )
            for action in actions:
                flags = action["endpoint_flags"]
                self.assertTrue(not flags["K4"] or flags["K3"])
                self.assertTrue(not flags["K3"] or flags["K2"])
                self.assertTrue(not flags["K2"] or flags["K1"])
                self.assertEqual(
                    action["control_ready_lineage_member"], flags["K1"]
                )

    def test_raw_behavior_and_contrast_counts_are_conserved(self) -> None:
        endpoints = ("K1", "K2", "K3", "K4")
        for slot in self.slots:
            actions = slot["actions"]
            valid = [action for action in actions if action["endpoint_flags"]["K1"]]
            child_universe = {
                action["child_behavior_hash"] for action in valid
            }
            bundle_universe = {
                action["formal_counterfactual_bundle_sha256"]
                for action in valid
            }
            counts = slot["counts"]
            self.assertEqual(
                counts["universe_counts"],
                {
                    "raw_syntactic_actions": 10,
                    "control_ready_actions": len(valid),
                    "unique_child_behaviors": len(child_universe),
                    "unique_formal_counterfactual_bundles": len(
                        bundle_universe
                    ),
                },
            )
            expected_raw = {
                endpoint: sum(
                    action["endpoint_flags"][endpoint] for action in actions
                )
                for endpoint in endpoints
            }
            expected_behavior = {
                endpoint: len(
                    {
                        action["child_behavior_hash"]
                        for action in valid
                        if action["endpoint_flags"][endpoint]
                    }
                )
                for endpoint in endpoints
            }
            expected_bundles = {
                endpoint: len(
                    {
                        action["formal_counterfactual_bundle_sha256"]
                        for action in valid
                        if action["endpoint_flags"][endpoint]
                    }
                )
                for endpoint in endpoints
            }
            self.assertEqual(
                counts["endpoint_counts"]["raw_syntactic_actions"],
                expected_raw,
            )
            self.assertEqual(
                counts["endpoint_counts"]["unique_child_behaviors"],
                expected_behavior,
            )
            self.assertEqual(
                counts["endpoint_counts"]
                ["unique_formal_counterfactual_bundles"],
                expected_bundles,
            )
            self.assertEqual(
                counts["endpoint_opportunity"],
                {
                    endpoint: expected_raw[endpoint] > 0
                    for endpoint in endpoints
                },
            )

        aggregate = self.landscape["aggregate"]
        self.assertEqual(
            aggregate["control_ready_action_count"],
            sum(
                slot["counts"]["universe_counts"]["control_ready_actions"]
                for slot in self.slots
            ),
        )
        for endpoint in endpoints:
            self.assertEqual(
                aggregate["raw_action_endpoint_counts"][endpoint],
                sum(
                    slot["counts"]["endpoint_counts"]
                    ["raw_syntactic_actions"][endpoint]
                    for slot in self.slots
                ),
            )
            self.assertEqual(
                aggregate["slot_opportunity_counts"][endpoint],
                sum(
                    slot["counts"]["endpoint_opportunity"][endpoint]
                    for slot in self.slots
                ),
            )

    def test_controls_use_same_frame_and_frozen_first_two(self) -> None:
        plan_worlds = {
            int(world["world_seed"]): world for world in self.plan["worlds"]
        }
        for slot in self.slots:
            focal_motif = motif_by_id(str(slot["motif_id"]))
            plan_world = plan_worlds[int(slot["world_seed"])]
            parent = dsl.parse_sexpr(str(plan_world["parent"]))
            for row in slot["actions"]:
                if not row["endpoint_flags"]["K1"]:
                    continue
                pool = row["full_replacement_pool_robustness"][
                    "replacement_outcomes"
                ]
                frozen = row["formal_first_two_replacements"]
                self.assertGreaterEqual(len(pool), 2)
                self.assertEqual(frozen, pool[:2])
                focal_action = _edit_action_from_row(
                    row, motif_id=str(slot["motif_id"])
                )
                for index, control in enumerate(pool):
                    control_motif = motif_by_id(str(control["motif_id"]))
                    self.assertEqual(control["pool_index"], index)
                    self.assertEqual(control_motif.stratum, focal_motif.stratum)
                    self.assertEqual(
                        control_motif.complexity_bucket,
                        focal_motif.complexity_bucket,
                    )
                    control_action = EditAction(
                        operation=focal_action.operation,
                        path=focal_action.path,
                        expected_old_subtree_hash=(
                            focal_action.expected_old_subtree_hash
                        ),
                        motif_id=control_motif.motif_id,
                        binary_operator=focal_action.binary_operator,
                        motif_side=focal_action.motif_side,
                    )
                    self.assertEqual(
                        focal_action.frame_key(focal_motif),
                        control_action.frame_key(control_motif),
                    )
                    replayed = apply_edit(parent, control_motif, control_action)
                    self.assertEqual(
                        dsl.canonical_hash(replayed),
                        control["child_canonical_hash"],
                    )
                    self.assertEqual(
                        dsl.behavior_hash(replayed, dsl.DOMAIN),
                        control["child_behavior_hash"],
                    )

                robustness = row["full_replacement_pool_robustness"]
                by_behavior: dict[str, bool] = {}
                for control in pool:
                    behavior = str(control["child_behavior_hash"])
                    outcome = bool(control["reaches_endpoint"])
                    if behavior in by_behavior:
                        self.assertEqual(by_behavior[behavior], outcome)
                    by_behavior[behavior] = outcome
                unique_successes = sum(by_behavior.values())
                unique_failures = len(by_behavior) - unique_successes
                self.assertEqual(
                    robustness["unique_behavior_pool_size"], len(by_behavior)
                )
                self.assertEqual(
                    robustness["formal_first_two_behavior_distinct"],
                    frozen[0]["child_behavior_hash"]
                    != frozen[1]["child_behavior_hash"],
                )
                self.assertEqual(
                    robustness["unique_behavior_endpoint_success_count"],
                    unique_successes,
                )
                self.assertEqual(
                    robustness["unique_behavior_endpoint_failure_count"],
                    unique_failures,
                )
                self.assertEqual(
                    robustness["unique_behavior_endpoint_failure_fraction"],
                    unique_failures / len(by_behavior),
                )

    def test_overlay_reproduces_every_arm_world_and_qualifying_slot(self) -> None:
        layer_names = ("L", "M", "D", "R", "S")
        for arm_id in CROSS_MODEL_ARM_IDS:
            expected_arm = self.analysis["joint_analysis"]["arms"][arm_id]
            observed_arm = self.overlay["arms"][arm_id]
            self.assertEqual(observed_arm["record_count"], 96)
            self.assertEqual(
                observed_arm["sealed_world_counts_K"],
                expected_arm["world_counts_K"],
            )
            self.assertEqual(
                observed_arm["reproduced_world_counts_K"],
                expected_arm["world_counts_K"],
            )
            self.assertIs(observed_arm["formal_K1_K4_reproduced"], True)
            self.assertIs(
                observed_arm["formal_qualifying_slot_ids_reproduced"], True
            )
            rows_by_world: dict[int, list[dict[str, object]]] = {}
            for row in observed_arm["slots"]:
                rows_by_world.setdefault(int(row["world_seed"]), []).append(row)
                flags = row["exact_action_hit"]
                self.assertTrue(not flags["K4"] or flags["K3"])
                self.assertTrue(not flags["K3"] or flags["K2"])
                self.assertTrue(not flags["K2"] or flags["K1"])

            for expected_world in expected_arm["worlds"]:
                rows = rows_by_world[int(expected_world["world_seed"])]
                expected_qualifying = expected_world[
                    "layered_qualifying_slot_ids"
                ]
                for layer in layer_names:
                    self.assertEqual(
                        [
                            row["slot_id"]
                            for row in rows
                            if row["formal_layer_flags"][layer]
                        ],
                        expected_qualifying[layer],
                    )
                self.assertEqual(
                    [
                        row["slot_id"]
                        for row in rows
                        if row["formal_layer_flags"]
                        ["weak_at_least_one_replacement_failure"]
                    ],
                    expected_qualifying[
                        "weak_at_least_one_replacement_failure"
                    ],
                )

    def test_landscape_source_manifest_mismatch_fails_closed(self) -> None:
        with (
            mock.patch(
                "src.spark_opportunity_map.source_manifest",
                return_value={"source_manifest_sha256": "0" * 64},
            ),
            self.assertRaisesRegex(
                OpportunityMapError,
                "different diagnostic source manifest",
            ),
        ):
            overlay_sealed_model_actions(
                self.plan,
                self.bundle,
                self.analysis,
                self.landscape,
            )

    def test_tampered_canonical_inputs_fail_closed(self) -> None:
        cases = []
        plan = copy.deepcopy(self.plan)
        plan["plan_sha256"] = "0" * 64
        cases.append((plan, self.bundle, self.analysis))
        bundle = copy.deepcopy(self.bundle)
        bundle["bundle_sha256"] = "0" * 64
        cases.append((self.plan, bundle, self.analysis))
        analysis = copy.deepcopy(self.analysis)
        analysis["analysis_sha256"] = "0" * 64
        cases.append((self.plan, self.bundle, analysis))

        for plan_value, bundle_value, analysis_value in cases:
            with self.subTest(
                plan_sha=plan_value.get("plan_sha256"),
                bundle_sha=bundle_value.get("bundle_sha256"),
                analysis_sha=analysis_value.get("analysis_sha256"),
            ):
                with (
                    mock.patch(
                        "src.spark_opportunity_map."
                        "build_action_opportunity_landscape",
                        side_effect=AssertionError("target landscape was entered"),
                    ) as landscape,
                    self.assertRaises(OpportunityMapError),
                ):
                    build_action_opportunity_map(
                        plan_value,
                        bundle_value,
                        analysis_value,
                    )
                landscape.assert_not_called()


if __name__ == "__main__":
    unittest.main()
