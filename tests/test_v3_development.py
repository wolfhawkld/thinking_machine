from __future__ import annotations

from copy import deepcopy
import json
import unittest

from src.pilot_checkpoint import canonical_json_bytes, sha256_json
from src.providers.openai_compatible import OpenAICompatibleGenerator
from src.staged_pilot_v3 import (
    AcceptedResponseContract,
    FrozenTransactionIdentity,
    route_binding_sha256,
)
from src.v3_development import (
    V3DevelopmentError,
    V3_GATE_SHARDS,
    V3_MAIN_SHARDS,
    V3_MODEL_STRATA,
    build_campaign_manifest,
    build_execution_plan,
    derive_route_binding_sha256,
    derive_stratum_config,
    freeze_v3_design,
    load_v3_template,
    transaction_identity_payload,
    unresolved_preflight_fields,
    validate_campaign_manifest,
    validate_live_v3_preflight,
    validate_v3_config,
)


TRANSPORT_PROFILE = "stdlib-urllib-one-shot-v1"
SOURCE_FILES = [
    {
        "path": "src/example.py",
        "size_bytes": 17,
        "sha256": "a" * 64,
    },
    {
        "path": "v3-development-spec.md",
        "size_bytes": 23,
        "sha256": "b" * 64,
    },
]
SOURCE_HASH = sha256_json(SOURCE_FILES)
SOURCE_MANIFEST = {
    "schema_version": 1,
    "created_at_utc": "2026-08-09T00:00:00+00:00",
    "source_manifest_sha256": SOURCE_HASH,
    "files": SOURCE_FILES,
    "environment": {
        "python_version": "3.test",
        "python_implementation": "CPython",
        "python_executable": "/fixture/python",
        "platform": "fixture-platform",
        "git_head": None,
    },
}


def _generator(*, base_url: str, model: str) -> OpenAICompatibleGenerator:
    return OpenAICompatibleGenerator(
        base_url=base_url,
        api_key="fixture-secret-never-frozen",
        model=model,
        seed_supported=False,
        timeout=120.0,
    )


def _response_contract(snapshot: str) -> AcceptedResponseContract:
    return AcceptedResponseContract(
        provider_models=(snapshot,),
        finish_reasons=("stop",),
        max_output_tokens=256,
        seed_supported=False,
        require_zero_reasoning_tokens=True,
        prompt_cache_mode="absent",
        provider_fingerprint_mode="absent",
    )


def _binding(
    *,
    provider: str,
    name: str,
    snapshot: str,
    base_url: str,
    artifact_sha256: str,
) -> dict[str, object]:
    request = _generator(base_url=base_url, model=name).sanitized_request_contract()
    # Keep the v3 fixture usable while the provider and preflight changes land
    # concurrently; the integration test below requires the provider to emit it.
    request.setdefault("transport_profile", TRANSPORT_PROFILE)
    response = _response_contract(snapshot).to_dict()
    binding = derive_route_binding_sha256(request, response)
    return {
        "provider": provider,
        "name": name,
        "snapshot": snapshot,
        "sanitized_request_contract": request,
        "accepted_response_contract": response,
        "canary_evidence": {
            "status": "passed",
            "artifact_sha256": artifact_sha256,
            "route_binding_sha256": binding,
            "contract_satisfied": True,
        },
    }


def _bindings() -> dict[str, dict[str, object]]:
    return {
        "official-deepseek-v4": _binding(
            provider="official-deepseek",
            name="request-deepseek-v4",
            snapshot="response-deepseek-v4-snapshot",
            base_url="https://deepseek-route.invalid/v1",
            artifact_sha256="b" * 64,
        ),
        "official-glm-5.2": _binding(
            provider="official-bigmodel",
            name="glm-5.2",
            snapshot="glm-5.2-response-snapshot",
            base_url="https://glm-route.invalid/v1",
            artifact_sha256="c" * 64,
        ),
    }


def _runtime_routes(
    bindings: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    return {
        stratum: {
            "sanitized_request_contract": deepcopy(
                binding["sanitized_request_contract"]
            ),
            "accepted_response_contract": deepcopy(
                binding["accepted_response_contract"]
            ),
        }
        for stratum, binding in bindings.items()
    }


class V3DevelopmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bindings = _bindings()
        cls.frozen, cls.plan = freeze_v3_design(
            load_v3_template(),
            model_bindings=cls.bindings,
            source_manifest_sha256=SOURCE_HASH,
        )

    def test_checked_in_template_is_inert_and_derives_valid_runner_configs(self) -> None:
        template = load_v3_template()
        self.assertEqual(
            unresolved_preflight_fields(template),
            template["pre_execution_required"],
        )
        self.assertEqual(len(template["pre_execution_required"]), 40)
        for model in template["model_strata"]:
            request = model["route_contract"]["sanitized_request_contract"]
            response = model["route_contract"]["accepted_response_contract"]
            self.assertIsNone(request["endpoint_sha256"])
            self.assertIsNone(request["transport_profile"])
            self.assertIsNone(response["provider_models"])
            self.assertIsNone(model["route_contract"]["route_binding_sha256"])
            self.assertTrue(
                all(value is None for value in model["canary_evidence"].values())
            )
        self.assertEqual(template["execution"]["sampling_base_seed"], 1729)
        for stratum in V3_MODEL_STRATA:
            main = derive_stratum_config(template, stratum, gate=False)
            gate = derive_stratum_config(template, stratum, gate=True)
            self.assertEqual(len(main["worlds"]), 12)
            self.assertEqual(gate["worlds"], [{"seed": 2000, "depth": 3}])
            self.assertEqual(set(main["arms"]), {"L", "H", "C", "E2"})

    def test_route_hash_matches_live_coordinator_contract_exactly(self) -> None:
        for binding in self.bindings.values():
            generator = _generator(
                base_url=(
                    "https://deepseek-route.invalid/v1"
                    if binding["provider"] == "official-deepseek"
                    else "https://glm-route.invalid/v1"
                ),
                model=str(binding["name"]),
            )
            request = generator.sanitized_request_contract()
            self.assertEqual(request["transport_profile"], TRANSPORT_PROFILE)
            contract = _response_contract(str(binding["snapshot"]))
            self.assertEqual(
                derive_route_binding_sha256(request, contract.to_dict()),
                route_binding_sha256(generator, contract),
            )

    def test_freeze_builds_deterministic_balanced_hashed_104_shard_plan(self) -> None:
        again_frozen, again_plan = freeze_v3_design(
            load_v3_template(),
            model_bindings=deepcopy(self.bindings),
            source_manifest_sha256=SOURCE_HASH,
        )
        self.assertEqual((self.frozen, self.plan), (again_frozen, again_plan))
        self.assertEqual(len(self.plan[:V3_GATE_SHARDS]), 8)
        self.assertEqual(len(self.plan[V3_GATE_SHARDS:]), V3_MAIN_SHARDS)
        self.assertEqual(sum(item["logical_calls"] for item in self.plan), 2080)
        self.assertEqual(
            self.frozen["execution"]["execution_plan_sha256"],
            sha256_json(self.plan),
        )
        self.assertEqual(build_execution_plan(self.frozen), self.plan)
        self.assertEqual(
            len({item["run_id"] for item in self.plan}),
            len(self.plan),
        )
        self.assertEqual(
            len({item["plan_entry_sha256"] for item in self.plan}),
            len(self.plan),
        )
        for index, item in enumerate(self.plan):
            self.assertEqual(item["shard_index"], index)
            self.assertEqual(item["sampling_base_seed"], 1729)
            basis = dict(item)
            stored_hash = basis.pop("plan_entry_sha256")
            self.assertEqual(stored_hash, sha256_json(basis))
        self.assertTrue(
            all(not item["development_outcome_eligible"] for item in self.plan[:8])
        )
        self.assertTrue(
            all(item["development_outcome_eligible"] for item in self.plan[8:])
        )

        main = self.plan[8:]
        for model_index in (0, 1):
            rows = [item for item in main if item["model_index"] == model_index]
            self.assertEqual(len(rows), 48)
            for arm_id in ("L", "H", "C", "E2"):
                self.assertEqual(sum(item["arm_id"] == arm_id for item in rows), 12)
                for position in range(4):
                    self.assertEqual(
                        sum(
                            item["arm_id"] == arm_id
                            and item["arm_position"] == position
                            for item in rows
                        ),
                        3,
                    )

        encoded = canonical_json_bytes(
            {"frozen": self.frozen, "plan": self.plan}
        ).decode("utf-8")
        for forbidden in (
            "https://deepseek-route.invalid",
            "https://glm-route.invalid",
            "fixture-secret-never-frozen",
            "hidden_law",
            "train_examples",
            "probe_examples",
            "test_examples",
            "candidate_expression",
            "api_key",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_missing_null_or_failed_canary_binding_is_rejected(self) -> None:
        cases: list[dict[str, dict[str, object]]] = []

        missing_stratum = deepcopy(self.bindings)
        del missing_stratum["official-glm-5.2"]
        cases.append(missing_stratum)

        missing_contract_field = deepcopy(self.bindings)
        del missing_contract_field["official-glm-5.2"][
            "sanitized_request_contract"
        ]["transport_profile"]
        cases.append(missing_contract_field)

        null_endpoint = deepcopy(self.bindings)
        null_endpoint["official-deepseek-v4"]["sanitized_request_contract"][
            "endpoint_sha256"
        ] = None
        cases.append(null_endpoint)

        failed_canary = deepcopy(self.bindings)
        failed_canary["official-deepseek-v4"]["canary_evidence"][
            "status"
        ] = "failed"
        cases.append(failed_canary)

        mismatched_canary = deepcopy(self.bindings)
        mismatched_canary["official-glm-5.2"]["canary_evidence"][
            "route_binding_sha256"
        ] = "d" * 64
        cases.append(mismatched_canary)

        for bindings in cases:
            with self.subTest(bindings=bindings):
                with self.assertRaises(V3DevelopmentError):
                    freeze_v3_design(
                        load_v3_template(),
                        model_bindings=bindings,
                        source_manifest_sha256=SOURCE_HASH,
                    )

        falsely_frozen = load_v3_template()
        falsely_frozen["status"] = "frozen-development-v3"
        falsely_frozen["pre_execution_required"] = []
        for model in falsely_frozen["model_strata"]:
            model["pre_execution_freeze_required"] = False
        with self.assertRaises(V3DevelopmentError):
            validate_v3_config(falsely_frozen, require_bound=True)

    def test_live_preflight_accepts_exact_routes_and_rejects_drift(self) -> None:
        runtime = _runtime_routes(self.bindings)
        observed = validate_live_v3_preflight(
            self.frozen,
            runtime_routes=runtime,
        )
        self.assertEqual(set(observed), set(V3_MODEL_STRATA))
        self.assertEqual(
            observed,
            {
                item["stratum_id"]: item["route_contract"][
                    "route_binding_sha256"
                ]
                for item in self.frozen["model_strata"]
            },
        )

        drifted_endpoint = deepcopy(runtime)
        drifted_endpoint["official-deepseek-v4"][
            "sanitized_request_contract"
        ]["endpoint_sha256"] = "e" * 64
        with self.assertRaisesRegex(V3DevelopmentError, "request route drifted"):
            validate_live_v3_preflight(
                self.frozen,
                runtime_routes=drifted_endpoint,
            )

        drifted_transport = deepcopy(runtime)
        drifted_transport["official-deepseek-v4"][
            "sanitized_request_contract"
        ]["transport_profile"] = "injected-may-retry"
        with self.assertRaisesRegex(V3DevelopmentError, "transport_profile"):
            validate_live_v3_preflight(
                self.frozen,
                runtime_routes=drifted_transport,
            )

        drifted_response = deepcopy(runtime)
        drifted_response["official-glm-5.2"]["accepted_response_contract"][
            "prompt_cache_mode"
        ] = "complete"
        with self.assertRaisesRegex(
            V3DevelopmentError,
            "accepted-response contract drifted",
        ):
            validate_live_v3_preflight(
                self.frozen,
                runtime_routes=drifted_response,
            )

        swapped = {
            V3_MODEL_STRATA[0]: deepcopy(runtime[V3_MODEL_STRATA[1]]),
            V3_MODEL_STRATA[1]: deepcopy(runtime[V3_MODEL_STRATA[0]]),
        }
        with self.assertRaisesRegex(V3DevelopmentError, "request route drifted"):
            validate_live_v3_preflight(self.frozen, runtime_routes=swapped)

    def test_frozen_route_or_plan_drift_is_rejected(self) -> None:
        drifted_route = deepcopy(self.frozen)
        drifted_route["model_strata"][0]["route_contract"][
            "sanitized_request_contract"
        ]["endpoint_sha256"] = "e" * 64
        with self.assertRaisesRegex(V3DevelopmentError, "route binding drifted"):
            validate_v3_config(drifted_route, require_bound=True)

        null_route = deepcopy(self.frozen)
        null_route["model_strata"][0]["route_contract"][
            "sanitized_request_contract"
        ]["transport_profile"] = None
        with self.assertRaises(V3DevelopmentError):
            validate_v3_config(null_route, require_bound=True)

        drifted_plan_hash = deepcopy(self.frozen)
        drifted_plan_hash["execution"]["execution_plan_sha256"] = "f" * 64
        with self.assertRaisesRegex(V3DevelopmentError, "execution-plan binding"):
            validate_v3_config(drifted_plan_hash, require_bound=True)

        drifted_sampling_seed = deepcopy(self.frozen)
        drifted_sampling_seed["execution"]["sampling_base_seed"] = 1730
        with self.assertRaisesRegex(V3DevelopmentError, "execution/retry"):
            validate_v3_config(drifted_sampling_seed, require_bound=True)

    def test_cross_stratum_route_aliasing_is_rejected(self) -> None:
        duplicated = deepcopy(self.bindings)
        duplicated["official-glm-5.2"] = deepcopy(
            duplicated["official-deepseek-v4"]
        )
        duplicated["official-glm-5.2"]["provider"] = "official-bigmodel"
        with self.assertRaisesRegex(V3DevelopmentError, "distinct exact routes"):
            freeze_v3_design(
                load_v3_template(),
                model_bindings=duplicated,
                source_manifest_sha256=SOURCE_HASH,
            )

    def test_plan_identity_changes_with_route_or_sampling_contract(self) -> None:
        changed = deepcopy(self.bindings)
        changed["official-glm-5.2"] = _binding(
            provider="official-bigmodel",
            name="glm-5.2",
            snapshot="glm-5.2-response-snapshot",
            base_url="https://glm-route-new.invalid/v1",
            artifact_sha256="d" * 64,
        )
        changed_frozen, changed_plan = freeze_v3_design(
            load_v3_template(),
            model_bindings=changed,
            source_manifest_sha256=SOURCE_HASH,
        )
        self.assertNotEqual(
            self.frozen["execution"]["execution_plan_sha256"],
            changed_frozen["execution"]["execution_plan_sha256"],
        )
        self.assertNotEqual(
            [item["run_id"] for item in self.plan],
            [item["run_id"] for item in changed_plan],
        )

    def test_campaign_manifest_and_transaction_identity_are_deterministic(self) -> None:
        manifest = build_campaign_manifest(
            self.frozen,
            self.plan,
            source_manifest=SOURCE_MANIFEST,
        )
        self.assertEqual(
            manifest,
            build_campaign_manifest(
                self.frozen,
                deepcopy(self.plan),
                source_manifest=deepcopy(SOURCE_MANIFEST),
            ),
        )
        self.assertEqual(
            manifest["execution_plan_sha256"],
            self.frozen["execution"]["execution_plan_sha256"],
        )
        self.assertEqual(manifest["total_shards"], 104)
        self.assertEqual(manifest["total_logical_calls"], 2080)
        self.assertEqual(manifest["frozen_config"], self.frozen)
        self.assertEqual(manifest["source_manifest"], SOURCE_MANIFEST)
        self.assertEqual(validate_campaign_manifest(manifest), manifest)
        detached_cached = validate_campaign_manifest(manifest)
        detached_cached["execution_plan"][0]["world_seed"] += 1
        self.assertEqual(validate_campaign_manifest(manifest), manifest)

        payload = transaction_identity_payload(manifest, self.plan[0])
        self.assertEqual(
            set(payload),
            {
                "campaign_manifest_payload_sha256",
                "execution_plan_sha256",
                "plan_entry_sha256",
                "run_id",
                "shard_index",
                "model_stratum",
                "phase",
                "world_seed",
                "depth",
                "arm_id",
            },
        )
        self.assertEqual(
            payload["campaign_manifest_payload_sha256"],
            sha256_json(manifest),
        )
        identity = FrozenTransactionIdentity(**payload)
        self.assertEqual(identity.to_dict(), payload)
        self.assertEqual(
            FrozenTransactionIdentity.from_plan_entry(
                campaign_manifest_payload_sha256=sha256_json(manifest),
                execution_plan_sha256=manifest["execution_plan_sha256"],
                entry=self.plan[0],
            ).to_dict(),
            payload,
        )

        tampered_entry = deepcopy(self.plan[0])
        tampered_entry["world_seed"] += 1
        with self.assertRaises(V3DevelopmentError):
            transaction_identity_payload(manifest, tampered_entry)

        tampered_manifest = deepcopy(manifest)
        tampered_manifest["execution_plan"][0]["world_seed"] += 1
        with self.assertRaises(V3DevelopmentError):
            transaction_identity_payload(tampered_manifest, self.plan[0])

        tampered_config = deepcopy(manifest)
        tampered_config["frozen_config"]["execution"]["sampling_base_seed"] += 1
        with self.assertRaises(V3DevelopmentError):
            transaction_identity_payload(tampered_config, self.plan[0])

        tampered_source_file = deepcopy(manifest)
        tampered_source_file["source_manifest"]["files"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(V3DevelopmentError, "file-list hash"):
            transaction_identity_payload(tampered_source_file, self.plan[0])

        mismatched_source = deepcopy(SOURCE_MANIFEST)
        mismatched_source["files"][0]["sha256"] = "e" * 64
        mismatched_source["source_manifest_sha256"] = sha256_json(
            mismatched_source["files"]
        )
        with self.assertRaisesRegex(V3DevelopmentError, "does not match config"):
            build_campaign_manifest(
                self.frozen,
                self.plan,
                source_manifest=mismatched_source,
            )

        encoded = canonical_json_bytes(manifest).decode("utf-8")
        self.assertNotIn("fixture-secret-never-frozen", encoded)
        self.assertNotIn("https://deepseek-route.invalid", encoded)
        self.assertNotIn("https://glm-route.invalid", encoded)

    def test_non_json_or_non_hash_inputs_fail_closed(self) -> None:
        bindings = deepcopy(self.bindings)
        bindings["official-glm-5.2"]["accepted_response_contract"][
            "provider_fingerprint_sha256"
        ] = []
        with self.assertRaises(V3DevelopmentError):
            freeze_v3_design(
                load_v3_template(),
                model_bindings=bindings,
                source_manifest_sha256=SOURCE_HASH,
            )
        with self.assertRaises(V3DevelopmentError):
            freeze_v3_design(
                load_v3_template(),
                model_bindings=self.bindings,
                source_manifest_sha256="not-a-hash",
            )


if __name__ == "__main__":
    unittest.main()
