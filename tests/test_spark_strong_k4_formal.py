from __future__ import annotations

from contextlib import ExitStack
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import spark_strong_k4_formal as formal
from src.providers.openai_compatible import (
    HTTPResponse,
    OpenAICompatibleGenerator,
    TransportError,
)
from src.spark_strong_k4_benchmark import (
    CANONICAL_ROUTE_IDS,
    FAIR_CONFIG_FILE_SHA256,
)
from src.staged_pilot_v3 import AcceptedResponseContract, route_binding_sha256


SOURCE_SHA256 = "a" * 64
PRIVATE_COMMITMENT_SHA256 = "b" * 64
PUBLIC_INNER_SHA256 = "c" * 64
PRIVATE_INNER_SHA256 = "d" * 64
CANARY_PLAN_INNER_SHA256 = "e" * 64
CANARY_PROMPT_SET_SHA256 = "f" * 64


class _ScienceTransport:
    def __init__(self, response_model: str, *, fail_on_call: int | None = None) -> None:
        self.response_model = response_model
        self.fail_on_call = fail_on_call
        self.calls: list[dict[str, object]] = []

    def post(self, **kwargs: object) -> HTTPResponse:
        self.calls.append(dict(kwargs))
        if len(self.calls) == self.fail_on_call:
            raise OSError("synthetic transport failure")
        payload = {
            "model": self.response_model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"expression": "Q00000000"}),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 40,
                "completion_tokens": 4,
                "total_tokens": 44,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        return HTTPResponse(200, json.dumps(payload).encode("utf-8"))


def _generator(
    model: str, transport: _ScienceTransport, *, timeout: float = 120.0
) -> OpenAICompatibleGenerator:
    return OpenAICompatibleGenerator(
        base_url="https://unit.invalid/v1",
        api_key="unit-secret",
        model=model,
        seed_supported=False,
        timeout=timeout,
        extra_body={"thinking": {"type": "disabled"}},
        transport=transport,
    )


class _FormalFixture:
    def __init__(self, directory: str) -> None:
        self.root = Path(directory)
        aliases = {
            "deepseek-flash": "deepseek-v4-flash",
            "deepseek-pro": "deepseek-v4-pro",
            "glm-5.2": "glm-5.2",
        }
        self.transports = {
            route_id: _ScienceTransport(model)
            for route_id, model in aliases.items()
        }
        self.generators = {
            route_id: _generator(aliases[route_id], self.transports[route_id])
            for route_id in CANONICAL_ROUTE_IDS
        }
        self.contracts = {
            route_id: AcceptedResponseContract(
                provider_models=(aliases[route_id],),
                finish_reasons=("stop", "length"),
                max_output_tokens=256,
                seed_supported=False,
                require_zero_reasoning_tokens=True,
                prompt_cache_mode="absent",
                provider_fingerprint_mode="absent",
            )
            for route_id in CANONICAL_ROUTE_IDS
        }
        self.public = self._public()
        self.private = self._private()
        self.canary_plan = {
            "canary_id": formal.canary.FAIR_CHOICE_CANARY_ID,
            "canary_plan_sha256": CANARY_PLAN_INNER_SHA256,
            "prompt_set_sha256": CANARY_PROMPT_SET_SHA256,
            "current_source_manifest_sha256": SOURCE_SHA256,
            "fair_config_file_sha256": FAIR_CONFIG_FILE_SHA256,
        }
        self.canary_plan_path = self.root / "canary-plan.json"
        self.canary_plan_path.write_bytes(formal._render_json_bytes(self.canary_plan))
        self.canary_artifacts: dict[str, dict[str, object]] = {}
        self.canary_paths: dict[str, Path] = {}
        for route_id in CANONICAL_ROUTE_IDS:
            artifact = {
                "route_id": route_id,
                "passed": True,
                "current_source_manifest_sha256": SOURCE_SHA256,
                "canary_artifact_sha256": hashlib.sha256(
                    f"canary:{route_id}".encode("ascii")
                ).hexdigest(),
                "request_model": aliases[route_id],
                "response_model": aliases[route_id],
                "sanitized_request_contract": self.generators[
                    route_id
                ].sanitized_request_contract(),
                "accepted_response_contract": self.contracts[route_id].to_dict(),
                "route_binding_sha256": route_binding_sha256(
                    self.generators[route_id], self.contracts[route_id]
                ),
            }
            path = self.root / f"canary-{route_id}.json"
            path.write_bytes(formal._render_json_bytes(artifact))
            self.canary_artifacts[route_id] = artifact
            self.canary_paths[route_id] = path
        with self.patches():
            self.plan = formal.build_fair_choice_formal_plan(
                self.public,
                self.private,
                canary_plan_path=self.canary_plan_path,
                canary_artifact_paths=self.canary_paths,
            )
        self.public_path = self.root / "public.json"
        self.private_path = self.root / "private.json"
        self.plan_path = self.root / "formal-plan.json"
        self.public_path.write_bytes(formal._render_json_bytes(self.public))
        self.private_path.write_bytes(formal._render_json_bytes(self.private))
        self.plan_path.write_bytes(formal._render_json_bytes(self.plan))
        self.plan_file_sha256 = hashlib.sha256(
            self.plan_path.read_bytes()
        ).hexdigest()

    def _public(self) -> dict[str, object]:
        tasks = []
        for index in range(64):
            prompt = f"neutral public prompt {index:02d}"
            tasks.append(
                {
                    "task_id": f"TASK-{index:014d}",
                    "rendered_prompt": prompt,
                    "prompt_sha256": hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest(),
                }
            )
        return {
            "schema_version": 1,
            "kind": "spark-strong-k4-fair-choice-public-manifest",
            "protocol_id": formal.PROTOCOL_ID,
            "task_count": 64,
            "current_source_manifest_sha256": SOURCE_SHA256,
            "fair_config_file_sha256": FAIR_CONFIG_FILE_SHA256,
            "private_design_commitment_sha256": PRIVATE_COMMITMENT_SHA256,
            "tasks": tasks,
            "public_manifest_sha256": PUBLIC_INNER_SHA256,
        }

    def _private(self) -> dict[str, object]:
        pairs = []
        for pair_index in range(32):
            pairs.append(
                {
                    "pair_id": f"PAIR-{pair_index:014d}",
                    "arms": {
                        "factual": {
                            "task_id": f"TASK-{2 * pair_index:014d}",
                        },
                        "sham": {
                            "task_id": f"TASK-{2 * pair_index + 1:014d}",
                        },
                    },
                }
            )
        return {
            "private_key_sha256": PRIVATE_INNER_SHA256,
            "sealed_input_identity": {
                "current_source_manifest_sha256": SOURCE_SHA256,
                "fair_config_file_sha256": FAIR_CONFIG_FILE_SHA256,
            },
            "pairs": pairs,
        }

    def patches(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(
            mock.patch.object(
                formal, "source_manifest", return_value={
                    "source_manifest_sha256": SOURCE_SHA256
                }
            )
        )
        stack.enter_context(
            mock.patch.object(formal.benchmark, "validate_public_manifest")
        )
        stack.enter_context(
            mock.patch.object(formal.benchmark, "validate_private_key")
        )
        stack.enter_context(
            mock.patch.object(formal.canary, "validate_fair_choice_canary_plan")
        )
        stack.enter_context(
            mock.patch.object(
                formal.canary, "validate_fair_choice_canary_artifact"
            )
        )
        stack.enter_context(
            mock.patch.object(formal.canary, "preflight_fair_choice_route")
        )
        return stack

    def run(self) -> dict[str, object]:
        with self.patches():
            return formal.run_fair_choice_science(
                plan_path=self.plan_path,
                expected_plan_file_sha256=self.plan_file_sha256,
                public_manifest_path=self.public_path,
                canary_plan_path=self.canary_plan_path,
                canary_artifact_paths=self.canary_paths,
                generators=self.generators,
            )


class FairChoiceScheduleTests(unittest.TestCase):
    def test_schedule_has_full_coverage_and_frozen_balance(self) -> None:
        task_ids = [f"TASK-{index:014d}" for index in range(64)]
        schedule, audit = formal.build_balanced_fair_choice_schedule(task_ids)

        self.assertEqual(len(schedule), 192)
        self.assertEqual(
            formal.ROUTE_PERMUTATION_MULTIPLICITIES,
            (10, 11, 11, 11, 11, 10),
        )
        for task_id in task_ids:
            rows = [row for row in schedule if row["task_id"] == task_id]
            self.assertEqual(
                {row["route_id"] for row in rows}, set(CANONICAL_ROUTE_IDS)
            )
        self.assertEqual(
            audit["route_position_counts"],
            {
                "deepseek-flash": [21, 22, 21],
                "deepseek-pro": [22, 20, 22],
                "glm-5.2": [21, 22, 21],
            },
        )
        self.assertEqual(audit["maximum_route_position_count_spread"], 2)
        self.assertTrue(audit["pairwise_orders_are_exactly_balanced_32_32"])
        self.assertEqual(set(audit["pairwise_order_counts"].values()), {32})


class FairChoiceFormalPlanTests(unittest.TestCase):
    def test_plan_binds_exact_files_bijection_routes_and_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            with fixture.patches():
                formal.validate_fair_choice_formal_plan(fixture.plan)

            files = fixture.plan["file_bindings"]
            self.assertEqual(
                files["public_manifest"]["file_sha256"],
                hashlib.sha256(fixture.public_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                files["private_key"]["file_sha256"],
                hashlib.sha256(fixture.private_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                files["canary_plan"]["file_sha256"],
                hashlib.sha256(fixture.canary_plan_path.read_bytes()).hexdigest(),
            )
            for route in fixture.plan["route_qualifications"]:
                route_id = route["route_id"]
                self.assertEqual(
                    route["canary_artifact_file_sha256"],
                    hashlib.sha256(
                        fixture.canary_paths[route_id].read_bytes()
                    ).hexdigest(),
                )
                self.assertTrue(route["passed"])
            self.assertEqual(
                len(fixture.plan["execution"]["schedule"]), 192
            )

    def test_builder_rejects_failed_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            failed = copy.deepcopy(fixture.canary_artifacts["glm-5.2"])
            failed["passed"] = False
            fixture.canary_paths["glm-5.2"].write_bytes(
                formal._render_json_bytes(failed)
            )
            with fixture.patches(), self.assertRaisesRegex(
                formal.FairChoiceFormalError, "did not pass"
            ):
                formal.build_fair_choice_formal_plan(
                    fixture.public,
                    fixture.private,
                    canary_plan_path=fixture.canary_plan_path,
                    canary_artifact_paths=fixture.canary_paths,
                )

    def test_materialize_uses_exact_loader_and_writes_three_0600_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            output = Path(directory) / "formal"
            with (
                fixture.patches(),
                mock.patch.object(
                    formal.benchmark,
                    "load_sealed_scan_result",
                    return_value={"sealed": True},
                ) as load,
                mock.patch.object(
                    formal.benchmark,
                    "build_fair_choice_benchmark",
                    return_value=(fixture.public, fixture.private),
                ) as build,
            ):
                report = formal.materialize_fair_choice_formal(
                    output_dir=output,
                    sealed_result_path=Path(directory) / "sealed.json",
                    canary_plan_path=fixture.canary_plan_path,
                    canary_artifact_paths=fixture.canary_paths,
                )

            load.assert_called_once_with(Path(directory) / "sealed.json")
            build.assert_called_once_with({"sealed": True})
            for name in ("public.json", "private.json", "formal-plan.json"):
                path = output / name
                self.assertTrue(path.is_file())
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(
                report["file_sha256"]["formal_plan"],
                hashlib.sha256((output / "formal-plan.json").read_bytes()).hexdigest(),
            )

    def test_cli_credentials_bootstrap_models_from_sealed_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deepseek_file = root / "deepseek.env"
            deepseek_file.write_text(
                "DEEPSEEK_BASE_URL=https://deepseek.invalid/v1\n"
                "DEEPSEEK_API_KEY=deepseek-secret\n",
                encoding="utf-8",
            )
            glm_file = root / "glm.env"
            glm_file.write_text(
                "TENCENT_BASE_URL=https://glm.invalid/v1\n"
                "TENCENT_MODEL=minimax-m3\n"
                "TENCENT_API_KEY=glm-secret\n",
                encoding="utf-8",
            )
            plan = {
                "execution": {
                    "request_model_aliases": {
                        "deepseek-flash": "deepseek-v4-flash",
                        "deepseek-pro": "deepseek-v4-pro",
                        "glm-5.2": "glm-5.2",
                    }
                }
            }
            args = argparse.Namespace(
                deepseek_env_prefix="DEEPSEEK",
                deepseek_env_file=deepseek_file,
                glm_env_prefix="TENCENT",
                glm_env_file=glm_file,
            )
            built: list[formal.ProviderCredentials] = []
            loader_calls: list[dict[str, object]] = []
            real_loader = formal.load_provider_credentials

            def load(**kwargs: object) -> formal.ProviderCredentials:
                loader_calls.append(dict(kwargs))
                return real_loader(**kwargs)

            def capture(credentials: formal.ProviderCredentials) -> object:
                built.append(credentials)
                return object()

            with (
                mock.patch.object(formal.os, "environ", {}),
                mock.patch.object(
                    formal, "load_provider_credentials", side_effect=load
                ),
                mock.patch.object(
                    formal, "build_v3_generator", side_effect=capture
                ),
            ):
                generators = formal._load_cli_generators(args, plan)

            self.assertEqual(set(generators), set(CANONICAL_ROUTE_IDS))
            self.assertEqual(
                loader_calls[0]["environ"]["DEEPSEEK_MODEL"],
                "deepseek-v4-flash",
            )
            self.assertEqual(
                loader_calls[1]["environ"]["TENCENT_MODEL"], "glm-5.2"
            )
            self.assertEqual(
                [credentials.model for credentials in built],
                ["deepseek-v4-flash", "deepseek-v4-pro", "glm-5.2"],
            )
            self.assertEqual(built[1].base_url, built[0].base_url)
            self.assertEqual(built[1].api_key, built[0].api_key)


class FairChoiceScienceExecutionTests(unittest.TestCase):
    def test_public_byte_drift_or_missing_canary_stops_before_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            fixture.public_path.write_bytes(fixture.public_path.read_bytes() + b" ")
            with fixture.patches(), self.assertRaisesRegex(
                formal.FairChoiceFormalError, "public manifest bytes"
            ):
                formal.run_fair_choice_science(
                    plan_path=fixture.plan_path,
                    expected_plan_file_sha256=fixture.plan_file_sha256,
                    public_manifest_path=fixture.public_path,
                    canary_plan_path=fixture.canary_plan_path,
                    canary_artifact_paths=fixture.canary_paths,
                    generators=fixture.generators,
                )
            self.assertEqual(
                sum(len(value.calls) for value in fixture.transports.values()), 0
            )

        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            fixture.canary_paths["deepseek-pro"].unlink()
            with fixture.patches(), self.assertRaisesRegex(
                formal.FairChoiceFormalError, "canary artifact JSON"
            ):
                formal.run_fair_choice_science(
                    plan_path=fixture.plan_path,
                    expected_plan_file_sha256=fixture.plan_file_sha256,
                    public_manifest_path=fixture.public_path,
                    canary_plan_path=fixture.canary_plan_path,
                    canary_artifact_paths=fixture.canary_paths,
                    generators=fixture.generators,
                )
            self.assertEqual(
                sum(len(value.calls) for value in fixture.transports.values()), 0
            )

    def test_all_three_adapters_preflight_before_first_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            wrong_transport = _ScienceTransport("glm-5.2")
            generators = dict(fixture.generators)
            generators["glm-5.2"] = _generator(
                "glm-5.2", wrong_transport, timeout=99.0
            )
            with fixture.patches(), self.assertRaisesRegex(
                formal.FairChoiceFormalError, "runtime request contract"
            ):
                formal.run_fair_choice_science(
                    plan_path=fixture.plan_path,
                    expected_plan_file_sha256=fixture.plan_file_sha256,
                    public_manifest_path=fixture.public_path,
                    canary_plan_path=fixture.canary_plan_path,
                    canary_artifact_paths=fixture.canary_paths,
                    generators=generators,
                )
            self.assertEqual(
                sum(len(value.calls) for value in fixture.transports.values()), 0
            )
            self.assertEqual(wrong_transport.calls, [])

    def test_complete_run_sends_only_public_prompt_and_never_reads_private(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            labels: list[str] = []
            original = formal._read_bound_json

            def recording_read(*args: object, **kwargs: object) -> object:
                labels.append(str(kwargs["label"]))
                return original(*args, **kwargs)

            with fixture.patches(), mock.patch.object(
                formal, "_read_bound_json", side_effect=recording_read
            ):
                bundle = formal.run_fair_choice_science(
                    plan_path=fixture.plan_path,
                    expected_plan_file_sha256=fixture.plan_file_sha256,
                    public_manifest_path=fixture.public_path,
                    canary_plan_path=fixture.canary_plan_path,
                    canary_artifact_paths=fixture.canary_paths,
                    generators=fixture.generators,
                )

            self.assertTrue(bundle["complete"])
            self.assertEqual(bundle["call_count"], 192)
            self.assertNotIn("private key", labels)
            by_prompt = {
                task["rendered_prompt"]: task["task_id"]
                for task in fixture.public["tasks"]
            }
            for transport in fixture.transports.values():
                self.assertEqual(len(transport.calls), 64)
                for call in transport.calls:
                    request = json.loads(call["body"].decode("utf-8"))
                    self.assertEqual(len(request["messages"]), 1)
                    self.assertEqual(request["messages"][0]["role"], "user")
                    prompt = request["messages"][0]["content"]
                    self.assertIn(prompt, by_prompt)
                    self.assertNotIn(by_prompt[prompt], call["body"].decode("utf-8"))
                    self.assertEqual(request["temperature"], 0.2)
                    self.assertEqual(request["max_tokens"], 256)
                    self.assertEqual(request["thinking"], {"type": "disabled"})

    def test_transport_failure_propagates_without_retry_or_complete_bundle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            fixture.transports["deepseek-flash"].fail_on_call = 2
            with fixture.patches(), self.assertRaises(TransportError):
                fixture.run()
            self.assertEqual(
                len(fixture.transports["deepseek-flash"].calls), 2
            )
            self.assertLess(
                sum(len(value.calls) for value in fixture.transports.values()), 192
            )


class FairChoiceAnalysisBarrierTests(unittest.TestCase):
    def test_private_key_is_read_only_after_complete_bundle_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = _FormalFixture(directory)
            bundle = fixture.run()
            bundle_path = Path(directory) / "bundle.json"
            bundle_path.write_bytes(formal._render_json_bytes(bundle))
            bundle_file_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()

            incomplete = copy.deepcopy(bundle)
            incomplete["complete"] = False
            unsigned = {
                key: value
                for key, value in incomplete.items()
                if key != "generation_bundle_sha256"
            }
            incomplete["generation_bundle_sha256"] = formal._sha256_json(unsigned)
            bundle_path.write_bytes(formal._render_json_bytes(incomplete))
            incomplete_file_sha256 = hashlib.sha256(
                bundle_path.read_bytes()
            ).hexdigest()
            labels: list[str] = []
            original = formal._read_bound_json

            def recording_read(*args: object, **kwargs: object) -> object:
                labels.append(str(kwargs["label"]))
                return original(*args, **kwargs)

            with (
                fixture.patches(),
                mock.patch.object(
                    formal, "_read_bound_json", side_effect=recording_read
                ),
                self.assertRaisesRegex(
                    formal.FairChoiceFormalError, "bundle identity"
                ),
            ):
                formal.analyze_fair_choice_science(
                    plan_path=fixture.plan_path,
                    expected_plan_file_sha256=fixture.plan_file_sha256,
                    public_manifest_path=fixture.public_path,
                    generation_bundle_path=bundle_path,
                    expected_generation_bundle_file_sha256=incomplete_file_sha256,
                    private_key_path=fixture.private_path,
                )
            self.assertNotIn("private key", labels)

            wrong_plan = copy.deepcopy(bundle)
            wrong_plan["formal_plan_file_sha256"] = "0" * 64
            wrong_unsigned = {
                key: value
                for key, value in wrong_plan.items()
                if key != "generation_bundle_sha256"
            }
            wrong_plan["generation_bundle_sha256"] = formal._sha256_json(
                wrong_unsigned
            )
            bundle_path.write_bytes(formal._render_json_bytes(wrong_plan))
            wrong_file_sha256 = hashlib.sha256(
                bundle_path.read_bytes()
            ).hexdigest()
            labels.clear()
            with (
                fixture.patches(),
                mock.patch.object(
                    formal, "_read_bound_json", side_effect=recording_read
                ),
                self.assertRaisesRegex(
                    formal.FairChoiceFormalError, "different formal plan"
                ),
            ):
                formal.analyze_fair_choice_science(
                    plan_path=fixture.plan_path,
                    expected_plan_file_sha256=fixture.plan_file_sha256,
                    public_manifest_path=fixture.public_path,
                    generation_bundle_path=bundle_path,
                    expected_generation_bundle_file_sha256=wrong_file_sha256,
                    private_key_path=fixture.private_path,
                )
            self.assertNotIn("private key", labels)

            bundle_path.write_bytes(formal._render_json_bytes(bundle))
            self.assertEqual(
                hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
                bundle_file_sha256,
            )
            labels.clear()
            fake_scores = {
                route_id: {"route": route_id} for route_id in CANONICAL_ROUTE_IDS
            }
            with (
                fixture.patches(),
                mock.patch.object(
                    formal, "_read_bound_json", side_effect=recording_read
                ),
                mock.patch.object(
                    formal.benchmark,
                    "score_model_responses",
                    side_effect=[fake_scores[route] for route in CANONICAL_ROUTE_IDS],
                ),
                mock.patch.object(
                    formal.benchmark,
                    "classify_joint_routes",
                    return_value={"joint_classification": "unit-complete"},
                ),
            ):
                analysis = formal.analyze_fair_choice_science(
                    plan_path=fixture.plan_path,
                    expected_plan_file_sha256=fixture.plan_file_sha256,
                    public_manifest_path=fixture.public_path,
                    generation_bundle_path=bundle_path,
                    expected_generation_bundle_file_sha256=bundle_file_sha256,
                    private_key_path=fixture.private_path,
                )
            self.assertEqual(labels[-1], "private key")
            self.assertTrue(
                analysis["private_key_loaded_after_complete_generation_barrier"]
            )


if __name__ == "__main__":
    unittest.main()
