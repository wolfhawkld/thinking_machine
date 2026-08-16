from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src import spark_cross_model as cross_model
from src.credentials import ProviderCredentials
from src.providers.openai_compatible import HTTPResponse, OpenAICompatibleGenerator
from src.provenance import PROJECT_ROOT, source_manifest
from src.spark_cross_model import (
    COMPARISON_ARM_ID,
    CROSS_MODEL_ARM_IDS,
    CROSS_MODEL_CALLS_PER_ARM,
    CROSS_MODEL_MOTIF_SELECTION_NAMESPACE,
    CROSS_MODEL_PROTOCOL_ID,
    CROSS_MODEL_SEED_NAMESPACE,
    CROSS_MODEL_TARGET_SEED_NAMESPACE,
    CROSS_MODEL_WORLD_SEEDS,
    REFERENCE_ARM_ID,
    CrossModelError,
    RouteArmSpec,
    analyze_cross_model,
    build_cross_model_plan,
    generate_cross_model,
    generate_cross_model_arm,
    main,
    route_arm_from_canary,
)
from src.spark_lineage import MOTIF_STRATA, build_motif_library


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fixed_route(arm_id: str) -> RouteArmSpec:
    frozen = cross_model._ROUTE_FREEZES[arm_id]
    return RouteArmSpec(
        arm_id=arm_id,
        model_stratum=frozen["model_stratum"],
        provider_profile=frozen["provider_profile"],
        request_model=frozen["request_model"],
        response_model=frozen["response_model"],
        sanitized_request_contract=copy.deepcopy(
            frozen["sanitized_request_contract"]
        ),
        canary_artifact_sha256=frozen["canary_artifact_sha256"],
        route_binding_sha256=frozen["route_binding_sha256"],
        accepted_response_contract=copy.deepcopy(
            frozen["accepted_response_contract"]
        ),
    )


def _fixed_routes() -> tuple[RouteArmSpec, RouteArmSpec]:
    return tuple(  # type: ignore[return-value]
        _fixed_route(arm_id) for arm_id in CROSS_MODEL_ARM_IDS
    )


def _toy_public_world(
    world_index: int,
    world_seed: int,
) -> dict[str, object]:
    return {
        "world_index": world_index,
        "world_seed": world_seed,
        "D0": [{"point": [0, 0], "label": bool(world_index % 2)}],
        "parent": f"(toy-parent {world_index})",
        "parent_canonical_hash": _sha256_json({"parent": world_index}),
        "allowed_paths": [
            {
                "path": [1, 1],
                "expected_old_subtree_hash": _sha256_json(
                    {"world": world_index, "path": [1, 1]}
                ),
                "old_subtree": "x",
            },
            {
                "path": [1, 2],
                "expected_old_subtree_hash": _sha256_json(
                    {"world": world_index, "path": [1, 2]}
                ),
                "old_subtree": "y",
            },
        ],
    }


def _build_target_free_test_plan() -> dict[str, object]:
    motifs = {
        stratum: next(
            motif for motif in build_motif_library() if motif.stratum == stratum
        )
        for stratum in MOTIF_STRATA
    }

    def select_motif(
        world_seed: int,
        slot_index: int,
        stratum: str,
        *,
        namespace: str,
    ):
        if namespace != CROSS_MODEL_MOTIF_SELECTION_NAMESPACE:
            raise AssertionError("builder used another motif namespace")
        digest = hashlib.sha256(
            f"toy:{world_seed}:{slot_index}:{stratum}".encode("ascii")
        ).hexdigest()
        return motifs[stratum], digest

    with (
        mock.patch(
            "src.spark_closure._target_free_public_world_entry",
            side_effect=_toy_public_world,
        ),
        mock.patch(
            "src.spark_closure._public_world_entry",
            side_effect=AssertionError("plan used the legacy world constructor"),
        ) as legacy_world,
        mock.patch(
            "src.spark_closure._select_motif",
            side_effect=select_motif,
        ),
        mock.patch(
            "src.spark_closure._derive_target_seed",
            side_effect=AssertionError("plan derived a hidden target"),
        ) as derive_target,
        mock.patch(
            "src.spark_closure.SparkCompressor",
            side_effect=AssertionError("plan ran a compressor"),
        ) as compressor,
        mock.patch(
            "src.spark_closure.enumerate_reachable_children",
            side_effect=AssertionError("plan enumerated target lineages"),
        ) as lineages,
    ):
        plan = build_cross_model_plan(_fixed_routes())
    derive_target.assert_not_called()
    legacy_world.assert_not_called()
    compressor.assert_not_called()
    lineages.assert_not_called()
    return plan


def _record_for(
    plan: dict[str, object],
    route: dict[str, object],
    slot: dict[str, object],
) -> dict[str, object]:
    world = plan["worlds"][slot["world_index"]]
    world_digest = cross_model._world_identity_sha256(world)
    slot_digest = cross_model._slot_identity_sha256(
        slot,
        world_identity_sha256=world_digest,
    )
    return {
        "serial_index": slot["serial_index"],
        "slot_id": slot["slot_id"],
        "world_index": slot["world_index"],
        "world_seed": slot["world_seed"],
        "slot_index": slot["slot_index"],
        "condition": slot["condition"],
        "motif_id": slot["motif_id"],
        "motif_stratum": slot["motif_stratum"],
        "world_identity_sha256": world_digest,
        "slot_identity_sha256": slot_digest,
        "action_parse_valid": True,
        "action": {"operation": "no_op"},
        "parse_failure": None,
        "telemetry": {"provider_model": route["response_model"]},
    }


def _fake_generation(
    plan: dict[str, object], arm_id: str
) -> dict[str, object]:
    route = next(route for route in plan["route_arms"] if route["arm_id"] == arm_id)
    records = [_record_for(plan, route, slot) for slot in plan["slots"]]
    return cross_model._seal_arm_generation(
        plan,
        route,
        records,
        paired_execution_schedule_validated=True,
    )


def _fake_bundle(
    plan: dict[str, object], generations: list[dict[str, object]]
) -> dict[str, object]:
    unsigned = {
        "schema_version": 1,
        "kind": "spark-cross-model-paired-generations",
        "protocol_id": plan["protocol_id"],
        "plan_sha256": plan["plan_sha256"],
        "execution_schedule_completed": True,
        "execution_trace": copy.deepcopy(plan["execution_schedule"]),
        "total_call_count": 192,
        "generations": generations,
    }
    return {**unsigned, "bundle_sha256": _sha256_json(unsigned)}


def _reseal_generation(generation: dict[str, object]) -> None:
    unsigned = {
        key: value
        for key, value in generation.items()
        if key != "generation_sha256"
    }
    generation["generation_sha256"] = _sha256_json(unsigned)


def _reseal_plan(plan: dict[str, object], *, public_changed: bool = False) -> None:
    if public_changed:
        plan["public_identity_sha256"] = _sha256_json(
            {
                "world_seeds": plan["world_seeds"],
                "worlds": plan["worlds"],
                "slots": plan["slots"],
            }
        )
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    plan["plan_sha256"] = _sha256_json(unsigned)


def _binding_for(arm_id: str) -> dict[str, object]:
    frozen = cross_model._ROUTE_FREEZES[arm_id]
    return {
        "provider": frozen["provider_profile"],
        "name": frozen["request_model"],
        "snapshot": frozen["response_model"],
        "sanitized_request_contract": copy.deepcopy(
            frozen["sanitized_request_contract"]
        ),
        "accepted_response_contract": copy.deepcopy(
            frozen["accepted_response_contract"]
        ),
        "canary_evidence": {
            "status": "passed",
            "artifact_sha256": frozen["canary_artifact_sha256"],
            "route_binding_sha256": frozen["route_binding_sha256"],
            "contract_satisfied": True,
        },
    }


class _MiniMaxTransport:
    def __init__(self, *, response_model: str = "minimax-m3") -> None:
        self.calls: list[dict[str, object]] = []
        self.response_model = response_model

    def post(self, **kwargs: object) -> HTTPResponse:
        self.calls.append(dict(kwargs))
        payload = {
            "model": self.response_model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"expression": "(no_op)"}),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        return HTTPResponse(200, json.dumps(payload).encode("utf-8"))


def _minimax_generator(
    *, model: str = "minimax-m3", response_model: str = "minimax-m3"
) -> tuple[OpenAICompatibleGenerator, _MiniMaxTransport]:
    transport = _MiniMaxTransport(response_model=response_model)
    generator = OpenAICompatibleGenerator(
        base_url="https://unit.invalid/v1",
        api_key="unit-secret",
        model=model,
        seed_supported=False,
        timeout=120.0,
        extra_body={"thinking": {"type": "disabled"}},
        transport=transport,
    )
    return generator, transport


class SparkCrossModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = _build_target_free_test_plan()
        cls.reference = _fake_generation(cls.plan, REFERENCE_ARM_ID)
        cls.comparison = _fake_generation(cls.plan, COMPARISON_ARM_ID)

    def _assert_analysis_rejected_before_target(
        self,
        plan: dict[str, object],
        generations: list[dict[str, object]],
    ) -> None:
        with (
            mock.patch(
                "src.spark_cross_model._run_joint_analysis_core",
                side_effect=AssertionError("joint core was entered"),
            ) as core,
            mock.patch("src.spark_closure._derive_target_seed") as derive,
            mock.patch("src.spark_closure.generate_spark_world") as build_world,
            mock.patch("src.spark_closure.SparkCompressor") as compressor,
        ):
            with self.assertRaises(CrossModelError):
                analyze_cross_model(plan, _fake_bundle(plan, generations))
        core.assert_not_called()
        derive.assert_not_called()
        build_world.assert_not_called()
        compressor.assert_not_called()

    def test_frozen_protocol_seeds_and_namespaces_match_registry(self) -> None:
        registry = json.loads(
            (PROJECT_ROOT / "configs" / "development-seed-registry.json").read_text(
                encoding="utf-8"
            )
        )
        record = registry["records"][-1]
        self.assertEqual(tuple(record["seeds"]), CROSS_MODEL_WORLD_SEEDS)
        self.assertEqual(
            record["draw"]["namespace"],
            f"{CROSS_MODEL_SEED_NAMESPACE}:world-seed",
        )
        self.assertEqual(self.plan["protocol_id"], CROSS_MODEL_PROTOCOL_ID)
        self.assertEqual(
            self.plan["target_seed_namespace"],
            CROSS_MODEL_TARGET_SEED_NAMESPACE,
        )
        self.assertEqual(
            self.plan["motif_selection_namespace"],
            CROSS_MODEL_MOTIF_SELECTION_NAMESPACE,
        )

    def test_route_arm_from_canary_uses_real_binding_and_fixed_files(self) -> None:
        for arm_id in CROSS_MODEL_ARM_IDS:
            frozen = cross_model._ROUTE_FREEZES[arm_id]
            credentials = ProviderCredentials(
                base_url="https://unit.invalid/v1",
                model=frozen["request_model"],
                api_key="unit-secret",
            )
            with mock.patch(
                "src.spark_cross_model.model_binding_from_canary",
                return_value=_binding_for(arm_id),
            ) as bind:
                route = route_arm_from_canary(arm_id, credentials)
            bind.assert_called_once_with(
                Path(frozen["canary_path"]),
                credentials,
                expected_stratum_id=frozen["model_stratum"],
            )
            self.assertEqual(route.to_dict(), _fixed_route(arm_id).to_dict())

    def test_canary_hash_or_model_error_is_target_free(self) -> None:
        credentials = ProviderCredentials(
            base_url="https://unit.invalid/v1",
            model="minimax-m3",
            api_key="unit-secret",
        )
        with tempfile.TemporaryDirectory() as directory:
            bad_canary = Path(directory) / "bad.json"
            bad_canary.write_text("{}\n", encoding="utf-8")
            with (
                mock.patch(
                    "src.spark_cross_model.model_binding_from_canary"
                ) as bind,
                mock.patch("src.spark_closure._derive_target_seed") as derive,
                mock.patch("src.spark_closure.SparkCompressor") as compressor,
            ):
                with self.assertRaisesRegex(CrossModelError, "file SHA-256"):
                    route_arm_from_canary(
                        COMPARISON_ARM_ID,
                        credentials,
                        canary_path=bad_canary,
                    )
            bind.assert_not_called()
            derive.assert_not_called()
            compressor.assert_not_called()

        wrong_model = ProviderCredentials(
            base_url="https://unit.invalid/v1",
            model="another-model",
            api_key="unit-secret",
        )
        with self.assertRaisesRegex(CrossModelError, "another request model"):
            route_arm_from_canary(COMPARISON_ARM_ID, wrong_model)

    def test_target_free_builder_freezes_grid_endpoints_and_schedule(self) -> None:
        self.assertEqual(len(self.plan["worlds"]), 32)
        self.assertEqual(len(self.plan["slots"]), 96)
        self.assertEqual(
            self.plan["stratum_counts"],
            {stratum: 24 for stratum in MOTIF_STRATA},
        )
        self.assertEqual(self.plan["prior_layered_v1"], cross_model.LAYERED_V1_ARTIFACTS)
        protocol = self.plan["protocol"]
        self.assertEqual(protocol["analysis_max_oracle_queries"], 4)
        self.assertFalse(protocol["pool_two_arms_as_64_worlds"])
        self.assertEqual(set(protocol["endpoint_definitions"]), {"K1", "K2", "K3", "K4"})
        schedule = self.plan["execution_schedule"]
        self.assertEqual(len(schedule), 192)
        for serial_index in range(96):
            pair = schedule[serial_index * 2 : serial_index * 2 + 2]
            expected = (
                CROSS_MODEL_ARM_IDS
                if serial_index % 2 == 0
                else tuple(reversed(CROSS_MODEL_ARM_IDS))
            )
            self.assertEqual(tuple(row["arm_id"] for row in pair), expected)
            self.assertEqual(
                [row["serial_index"] for row in pair],
                [serial_index, serial_index],
            )
        self.assertTrue(
            all(
                "target_seed" not in world
                and "target_index" not in world
                and "target_canonical_hash" not in world
                for world in self.plan["worlds"]
            )
        )

    def test_public_world_and_slot_schemas_are_closed(self) -> None:
        for collection, field, message in (
            ("worlds", "target_index", "world entry uses a non-public schema"),
            ("worlds", "evidence", "world entry uses a non-public schema"),
            ("slots", "test_outcome", "slot entry uses a non-public schema"),
        ):
            drifted = copy.deepcopy(self.plan)
            drifted[collection][0][field] = "forbidden"
            _reseal_plan(drifted, public_changed=True)
            with self.assertRaisesRegex(CrossModelError, message):
                cross_model._validate_plan(drifted)

    def test_single_arm_generation_validates_contract_and_emits_partials(self) -> None:
        generator, transport = _minimax_generator()
        partials: list[dict[str, object]] = []
        with (
            mock.patch.object(
                OpenAICompatibleGenerator,
                "sanitized_request_contract",
                return_value=copy.deepcopy(cross_model._MINIMAX_REQUEST_CONTRACT),
            ),
            mock.patch(
                "src.spark_cross_model.route_binding_sha256",
                return_value=cross_model.MINIMAX_ROUTE_BINDING_SHA256,
            ),
            mock.patch("src.spark_closure._derive_target_seed") as derive,
            mock.patch("src.spark_closure.SparkCompressor") as compressor,
        ):
            artifact = generate_cross_model_arm(
                self.plan,
                COMPARISON_ARM_ID,
                generator,
                progress_callback=lambda partial: partials.append(dict(partial)),
            )
        derive.assert_not_called()
        compressor.assert_not_called()
        self.assertEqual(len(transport.calls), 96)
        self.assertEqual(artifact["call_count"], 96)
        self.assertIs(artifact["live_response_contract_validated"], True)
        self.assertIs(artifact["paired_execution_schedule_validated"], False)
        self.assertEqual(len(partials), 96)
        self.assertEqual(partials[-1]["call_count"], 96)
        self.assertFalse(
            partials[-1]["generation_complete_before_joint_target_analysis"]
        )
        self.assertFalse(partials[-1]["resume_supported"])
        self._assert_analysis_rejected_before_target(
            self.plan,
            [self.reference, artifact],
        )

    def test_wrong_request_model_is_rejected_before_network_or_target(self) -> None:
        generator, transport = _minimax_generator(model="wrong-model")
        with (
            mock.patch("src.spark_closure._derive_target_seed") as derive,
            mock.patch("src.spark_closure.generate_spark_world") as build_world,
            mock.patch("src.spark_closure.SparkCompressor") as compressor,
        ):
            with self.assertRaisesRegex(CrossModelError, "request model differs"):
                generate_cross_model_arm(self.plan, COMPARISON_ARM_ID, generator)
        self.assertEqual(transport.calls, [])
        derive.assert_not_called()
        build_world.assert_not_called()
        compressor.assert_not_called()

    def test_wrong_response_alias_is_rejected_before_target(self) -> None:
        generator, transport = _minimax_generator(response_model="another-model")
        with (
            mock.patch.object(
                OpenAICompatibleGenerator,
                "sanitized_request_contract",
                return_value=copy.deepcopy(cross_model._MINIMAX_REQUEST_CONTRACT),
            ),
            mock.patch(
                "src.spark_cross_model.route_binding_sha256",
                return_value=cross_model.MINIMAX_ROUTE_BINDING_SHA256,
            ),
            mock.patch("src.spark_closure._derive_target_seed") as derive,
            mock.patch("src.spark_closure.generate_spark_world") as build_world,
            mock.patch("src.spark_closure.SparkCompressor") as compressor,
        ):
            with self.assertRaisesRegex(
                CrossModelError,
                "provider response violates the route response contract",
            ):
                generate_cross_model_arm(self.plan, COMPARISON_ARM_ID, generator)
        self.assertEqual(len(transport.calls), 1)
        derive.assert_not_called()
        build_world.assert_not_called()
        compressor.assert_not_called()

    def test_paired_runner_uses_frozen_alternating_order(self) -> None:
        generators = {
            REFERENCE_ARM_ID: _minimax_generator()[0],
            COMPARISON_ARM_ID: _minimax_generator()[0],
        }
        observed: list[tuple[int, str]] = []

        def generated(
            plan,
            slot,
            *,
            slot_digest,
            world_digest,
            route,
            generator,
            contract,
        ):
            del slot_digest, world_digest, generator, contract
            observed.append((slot["serial_index"], route["arm_id"]))
            return _record_for(plan, route, slot)

        with (
            mock.patch(
                "src.spark_cross_model._preflight_live_route",
                side_effect=lambda generator, route, max_output_tokens: (
                    cross_model._accepted_response_contract(
                        route["accepted_response_contract"]
                    )
                ),
            ),
            mock.patch(
                "src.spark_cross_model._generate_record",
                side_effect=generated,
            ),
        ):
            bundle = generate_cross_model(self.plan, generators)
        expected = [
            (row["serial_index"], row["arm_id"])
            for row in self.plan["execution_schedule"]
        ]
        self.assertEqual(observed, expected)
        self.assertEqual(bundle["total_call_count"], 192)
        self.assertTrue(bundle["execution_schedule_completed"])
        self.assertEqual(bundle["execution_trace"], self.plan["execution_schedule"])
        self.assertEqual(
            [generation["call_count"] for generation in bundle["generations"]],
            [96, 96],
        )
        tampered = copy.deepcopy(bundle)
        tampered["execution_trace"][0], tampered["execution_trace"][1] = (
            tampered["execution_trace"][1],
            tampered["execution_trace"][0],
        )
        unsigned = {
            key: value for key, value in tampered.items() if key != "bundle_sha256"
        }
        tampered["bundle_sha256"] = _sha256_json(unsigned)
        with self.assertRaisesRegex(CrossModelError, "bundle is malformed"):
            analyze_cross_model(self.plan, tampered)

    def test_incomplete_single_duplicate_and_identity_drift_stop_before_core(self) -> None:
        short = copy.deepcopy(self.reference)
        short["records"].pop()
        short["call_count"] = 95
        _reseal_generation(short)
        cases = (
            [short, self.comparison],
            [self.reference],
            [self.reference, copy.deepcopy(self.reference)],
        )
        for generations in cases:
            with self.subTest(call_counts=[item["call_count"] for item in generations]):
                self._assert_analysis_rejected_before_target(self.plan, generations)

        drifted = copy.deepcopy(self.comparison)
        drifted["records"][0]["motif_id"] = "forbidden:drift"
        _reseal_generation(drifted)
        self._assert_analysis_rejected_before_target(
            self.plan,
            [self.reference, drifted],
        )

    def test_source_manifest_drift_stops_network_core_and_target(self) -> None:
        drifted = copy.deepcopy(self.plan)
        drifted["source_manifest_sha256"] = "0" * 64
        _reseal_plan(drifted)
        generator, transport = _minimax_generator()
        with (
            mock.patch(
                "src.spark_cross_model._run_joint_analysis_core",
                side_effect=AssertionError("joint core was entered"),
            ) as core,
            mock.patch("src.spark_closure._derive_target_seed") as derive,
            mock.patch("src.spark_closure.generate_spark_world") as build_world,
            mock.patch("src.spark_closure.SparkCompressor") as compressor,
        ):
            with self.assertRaisesRegex(CrossModelError, "source manifest drifted"):
                generate_cross_model_arm(drifted, COMPARISON_ARM_ID, generator)
            with self.assertRaisesRegex(CrossModelError, "source manifest drifted"):
                analyze_cross_model(
                    drifted,
                    _fake_bundle(drifted, [self.reference, self.comparison]),
                )
        self.assertEqual(transport.calls, [])
        core.assert_not_called()
        derive.assert_not_called()
        build_world.assert_not_called()
        compressor.assert_not_called()

    def test_valid_barrier_enters_only_the_fixed_joint_core(self) -> None:
        synthetic = {
            "joint_classification": "paired_cross_model_replication_not_observed",
            "pooled_64_world_analysis_performed": False,
        }
        with mock.patch(
            "src.spark_cross_model._run_joint_analysis_core",
            return_value=synthetic,
        ) as core:
            report = analyze_cross_model(
                self.plan,
                _fake_bundle(self.plan, [self.comparison, self.reference]),
            )
        core.assert_called_once()
        self.assertEqual(report["joint_analysis"], synthetic)
        self.assertTrue(report["both_96_record_arms_validated_before_analysis"])

    def test_paired_four_cell_tables_use_worlds_not_pooled_arms(self) -> None:
        seeds = (11, 22, 33, 44)
        reference = []
        comparison = []
        reference_flags = (True, True, False, False)
        comparison_flags = (True, False, True, False)
        for seed, ref, comp in zip(
            seeds, reference_flags, comparison_flags, strict=True
        ):
            reference.append(
                {"world_seed": seed, "endpoints": {name: ref for name in "LMDR"}}
            )
            comparison.append(
                {"world_seed": seed, "endpoints": {name: comp for name in "LMDR"}}
            )
        tables = cross_model._paired_four_cell_tables(reference, comparison)
        for layer in ("K1", "K2", "K3", "K4"):
            self.assertEqual(
                tables[layer]["counts"],
                {
                    "both": 1,
                    "reference_only": 1,
                    "comparison_only": 1,
                    "neither": 1,
                },
            )
            self.assertEqual(tables[layer]["world_denominator"], 4)
        self.assertEqual(
            cross_model._joint_classification(
                {"K1": 3, "K4": 2}, {"K1": 3, "K4": 2}
            ),
            "paired_cross_model_replication_observed",
        )
        self.assertEqual(
            cross_model._joint_classification(
                {"K1": 3, "K4": 2}, {"K1": 3, "K4": 0}
            ),
            "mixed_model_robustness_evidence",
        )
        self.assertEqual(
            cross_model._joint_classification(
                {"K1": 3, "K4": 0}, {"K1": 0, "K4": 0}
            ),
            "paired_cross_model_replication_not_observed",
        )

    def test_joint_core_reuses_one_synthetic_world_context_for_both_arms(self) -> None:
        toy_seeds = (101, 202)
        parent = object()
        target = object()
        toy_worlds = [
            SimpleNamespace(
                train=(SimpleNamespace(point=(0, 0), label=False),),
                world_hash=f"world-{seed}",
                target_index=index,
                target=target,
            )
            for index, seed in enumerate(toy_seeds)
        ]
        plan = {
            "target_seed_namespace": "toy-targets",
            "worlds": [
                {
                    "world_seed": seed,
                    "D0": [{"point": [0, 0], "label": False}],
                    "parent": "toy-parent",
                    "parent_canonical_hash": "parent-hash",
                }
                for seed in toy_seeds
            ],
            "slots": [
                {
                    "serial_index": index,
                    "slot_id": f"toy-slot-{index}",
                    "world_seed": seed,
                    "motif_stratum": MOTIF_STRATA[index],
                }
                for index, seed in enumerate(toy_seeds)
            ],
            "route_arms": [route.to_dict() for route in _fixed_routes()],
        }
        artifacts = {
            REFERENCE_ARM_ID: {
                "records": [
                    {"serial_index": index, "action": {"operation": "no_op"}}
                    for index in range(2)
                ]
            },
            COMPARISON_ARM_ID: {
                "records": [
                    {
                        "serial_index": index,
                        "action": {"operation": "replace", "path": [1, 1]},
                    }
                    for index in range(2)
                ]
            },
        }

        class Compressor:
            def __init__(self, world) -> None:
                self.world = world

            def run(self, candidate, *, max_rounds):
                return SimpleNamespace(candidate=candidate, max_rounds=max_rounds)

        def row(*, slot, parsed, **kwargs):
            del kwargs
            base = {
                "slot_id": slot["slot_id"],
                "condition": "motif",
                "motif_stratum": slot["motif_stratum"],
                "strict_event": False,
            }
            if parsed.operation == "no_op":
                return {**base, "lineage_valid": False}
            return {
                **base,
                "lineage_valid": True,
                "child_direct_hit": False,
                "child_trajectory": {
                    "truth_retained": True,
                    "N_T": 1,
                    "full_domain_recovered": True,
                    "positive_non_match_contraction": True,
                    "rounds_completed": 1,
                },
                "parent_trajectory": {"exact_identification": False, "N_T": 2},
                "matched_replacements": [
                    {"trajectory": {"exact_identification": False, "N_T": 2}},
                    {"trajectory": {"exact_identification": False, "N_T": 3}},
                ],
            }

        with (
            mock.patch(
                "src.spark_closure._target_seed_for_namespace",
                side_effect=(9001, 9002),
            ) as derive,
            mock.patch(
                "src.spark_closure.generate_spark_world",
                side_effect=toy_worlds,
            ) as build_world,
            mock.patch("src.spark_closure.select_parent", return_value=parent),
            mock.patch("src.spark_cross_model.dsl.canonical_hash") as canonical_hash,
            mock.patch("src.spark_cross_model.dsl.to_sexpr", return_value="toy-parent"),
            mock.patch("src.spark_closure.SparkCompressor", side_effect=Compressor) as compressors,
            mock.patch(
                "src.spark_closure.enumerate_reachable_children",
                return_value=(),
            ) as lineages,
            mock.patch("src.spark_closure._analyze_factual_slot", side_effect=row),
        ):
            canonical_hash.side_effect = lambda value: (
                "parent-hash" if value is parent else "target-hash"
            )
            result = cross_model._run_joint_analysis_core(plan, artifacts)
        self.assertEqual(derive.call_count, 2)
        self.assertEqual(build_world.call_count, 2)
        self.assertEqual(compressors.call_count, 2)
        self.assertEqual(lineages.call_count, 2)
        self.assertEqual(result["shared_target_world_count"], 2)
        self.assertEqual(
            result["arms"][REFERENCE_ARM_ID]["world_counts_K"],
            {"K1": 0, "K2": 0, "K3": 0, "K4": 0},
        )
        self.assertEqual(
            result["arms"][COMPARISON_ARM_ID]["world_counts_K"],
            {"K1": 2, "K2": 2, "K3": 2, "K4": 2},
        )
        self.assertFalse(result["pooled_64_world_analysis_performed"])

    def test_cli_generate_requires_execute_before_reading_or_network(self) -> None:
        with (
            mock.patch("src.spark_cross_model._read_json") as read_json,
            mock.patch("src.spark_cross_model.generate_cross_model") as generate,
        ):
            with self.assertRaises(SystemExit):
                main(
                    [
                        "generate",
                        "--plan",
                        "unused.json",
                        "--output",
                        "unused-output.json",
                    ]
                )
        read_json.assert_not_called()
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
