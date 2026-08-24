from __future__ import annotations

import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from src import spark_strong_k4_canary as canary
from src.providers.openai_compatible import (
    HTTPResponse,
    OpenAICompatibleGenerator,
    TransportError,
)
from src.runner import GenerationResponse
from src.spark_lineage import MOTIF_STRATA


def _route(plan: dict[str, object], route_id: str) -> dict[str, object]:
    return next(
        route
        for route in plan["route_expectations"]
        if route["route_id"] == route_id
    )


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_all_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


class _OpaqueChoiceTransport:
    def __init__(
        self,
        *,
        response_model: str = "glm-5.2",
        valid_choice: bool = True,
        fail_on_call: int | None = None,
    ) -> None:
        self.response_model = response_model
        self.valid_choice = valid_choice
        self.fail_on_call = fail_on_call
        self.calls: list[dict[str, object]] = []

    def post(self, **kwargs: object) -> HTTPResponse:
        self.calls.append(dict(kwargs))
        if len(self.calls) == self.fail_on_call:
            raise OSError("synthetic transport failure")
        request = json.loads(kwargs["body"].decode("utf-8"))
        prompt = request["messages"][0]["content"]
        option_ids = re.findall(r"(?m)^  (Q[0-9A-F]{8}):", prompt)
        if len(option_ids) != 10:
            raise AssertionError("test transport did not receive an opaque prompt")
        expression = option_ids[0] if self.valid_choice else "QFFFFFFFF"
        payload = {
            "model": self.response_model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({"expression": expression}),
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 4,
                "total_tokens": 104,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        return HTTPResponse(200, json.dumps(payload).encode("utf-8"))


def _glm_generator(
    transport: _OpaqueChoiceTransport,
    *,
    model: str = "glm-5.2",
) -> OpenAICompatibleGenerator:
    return OpenAICompatibleGenerator(
        base_url="https://unit.invalid/v1",
        api_key="unit-secret",
        model=model,
        seed_supported=False,
        timeout=120.0,
        extra_body={"thinking": {"type": "disabled"}},
        transport=transport,
    )


class FairChoiceCanaryPlanTests(unittest.TestCase):
    def test_plan_is_four_strata_target_free_and_route_bound(self) -> None:
        with (
            mock.patch(
                "src.spark_closure._derive_target_seed",
                side_effect=AssertionError("canary derived a target"),
            ) as target,
            mock.patch(
                "src.spark_closure.SparkCompressor",
                side_effect=AssertionError("canary ran a compressor"),
            ) as compressor,
            mock.patch(
                "src.spark_lineage.enumerate_reachable_children",
                side_effect=AssertionError("canary evaluated lineage endpoints"),
            ) as lineage,
        ):
            plan = canary.build_fair_choice_canary_plan()

        target.assert_not_called()
        compressor.assert_not_called()
        lineage.assert_not_called()
        canary.validate_fair_choice_canary_plan(plan)
        self.assertEqual(plan["world_seed"], 1000)
        self.assertEqual(len(plan["tasks"]), 4)
        self.assertEqual(
            [task["motif_stratum"] for task in plan["tasks"]],
            list(MOTIF_STRATA),
        )
        self.assertEqual(
            [route["route_id"] for route in plan["route_expectations"]],
            ["deepseek-flash", "deepseek-pro", "glm-5.2"],
        )
        self.assertTrue(plan["protocol"]["world_is_retired_from_formal_evidence"])
        self.assertFalse(plan["protocol"]["hidden_target_derived"])
        self.assertFalse(plan["protocol"]["K1_K2_K3_K4_evaluated"])
        self.assertFalse(plan["protocol"]["compressor_run"])
        self.assertTrue(
            {"target_seed", "target_index", "endpoint_flags"}.isdisjoint(
                _all_keys(plan)
            )
        )
        for task in plan["tasks"]:
            self.assertEqual(len(task["opaque_option_ids"]), 10)
            self.assertEqual(len(set(task["opaque_option_ids"])), 10)
            self.assertNotIn(task["task_id"], task["rendered_prompt"])

    def test_plan_tamper_is_rejected(self) -> None:
        plan = canary.build_fair_choice_canary_plan()
        drifted = copy.deepcopy(plan)
        drifted["protocol"]["logical_calls_per_route"] = 3
        with self.assertRaisesRegex(canary.FairChoiceCanaryError, "fixed design"):
            canary.validate_fair_choice_canary_plan(drifted)


class FairChoiceCanaryExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = canary.build_fair_choice_canary_plan()
        self.glm_route = _route(self.plan, "glm-5.2")

    def _run(
        self, transport: _OpaqueChoiceTransport
    ) -> tuple[dict[str, object], OpenAICompatibleGenerator]:
        generator = _glm_generator(transport)
        with mock.patch.object(
            OpenAICompatibleGenerator,
            "sanitized_request_contract",
            return_value=copy.deepcopy(
                self.glm_route["sanitized_request_contract"]
            ),
        ):
            artifact = canary.run_fair_choice_canary(
                self.plan,
                "glm-5.2",
                generator,
            )
        return artifact, generator

    def test_valid_opaque_choices_pass_and_wire_payload_contains_no_task_id(self) -> None:
        transport = _OpaqueChoiceTransport()
        artifact, _generator = self._run(transport)

        canary.validate_fair_choice_canary_artifact(self.plan, artifact)
        self.assertTrue(artifact["passed"])
        self.assertTrue(artifact["contract_satisfied"])
        self.assertEqual(artifact["call_count"], 4)
        self.assertEqual(artifact["valid_choice_count"], 4)
        self.assertEqual(artifact["invalid_choice_count"], 0)
        self.assertEqual(len(transport.calls), 4)
        for task, call in zip(self.plan["tasks"], transport.calls, strict=True):
            request = json.loads(call["body"].decode("utf-8"))
            self.assertEqual(
                set(request),
                {
                    "model",
                    "messages",
                    "temperature",
                    "max_tokens",
                    "response_format",
                    "thinking",
                },
            )
            self.assertEqual(request["model"], "glm-5.2")
            self.assertEqual(request["temperature"], 0.2)
            self.assertEqual(request["max_tokens"], 256)
            self.assertEqual(request["thinking"], {"type": "disabled"})
            self.assertEqual(
                request["messages"],
                [{"role": "user", "content": task["rendered_prompt"]}],
            )
            self.assertNotIn(task["task_id"], call["body"].decode("utf-8"))

    def test_unlisted_but_well_formed_choice_completes_and_fails_content_gate(self) -> None:
        transport = _OpaqueChoiceTransport(valid_choice=False)
        artifact, _generator = self._run(transport)

        canary.validate_fair_choice_canary_artifact(self.plan, artifact)
        self.assertFalse(artifact["passed"])
        self.assertTrue(artifact["contract_satisfied"])
        self.assertEqual(artifact["valid_choice_count"], 0)
        self.assertEqual(artifact["invalid_choice_count"], 4)
        self.assertEqual(artifact["candidate_format_counts"], {"json_expression": 4})
        self.assertEqual(len(transport.calls), 4)

    def test_request_contract_drift_stops_before_first_call(self) -> None:
        transport = _OpaqueChoiceTransport()
        generator = _glm_generator(transport, model="wrong-model")
        with self.assertRaisesRegex(canary.FairChoiceCanaryError, "request model"):
            canary.run_fair_choice_canary(
                self.plan,
                "glm-5.2",
                generator,
            )
        self.assertEqual(transport.calls, [])

    def test_transport_failure_propagates_without_artifact_or_retry(self) -> None:
        transport = _OpaqueChoiceTransport(fail_on_call=3)
        generator = _glm_generator(transport)
        with mock.patch.object(
            OpenAICompatibleGenerator,
            "sanitized_request_contract",
            return_value=copy.deepcopy(
                self.glm_route["sanitized_request_contract"]
            ),
        ):
            with self.assertRaises(TransportError):
                canary.run_fair_choice_canary(
                    self.plan,
                    "glm-5.2",
                    generator,
                )
        self.assertEqual(len(transport.calls), 3)

    def test_rehashed_record_tamper_is_rejected(self) -> None:
        artifact, _generator = self._run(_OpaqueChoiceTransport())
        tampered = copy.deepcopy(artifact)
        tampered["records"][0]["selected_option_id"] = "QFFFFFFFF"
        unsigned = {
            key: value
            for key, value in tampered.items()
            if key != "canary_artifact_sha256"
        }
        tampered["canary_artifact_sha256"] = canary._sha256_json(unsigned)
        with self.assertRaisesRegex(canary.FairChoiceCanaryError, "valid opaque"):
            canary.validate_fair_choice_canary_artifact(self.plan, tampered)

    def test_current_deepseek_cache_and_fingerprint_contract_is_rederived(self) -> None:
        generator = _glm_generator(
            _OpaqueChoiceTransport(),
            model="deepseek-v4-flash",
        )
        responses = [
            GenerationResponse(
                expression="Q12345678",
                input_tokens=100,
                output_tokens=4,
                latency_ms=1.0,
                provider_request_count=1,
                seed_supported=False,
                provider_model="deepseek-v4-flash",
                finish_reason="stop",
                prompt_cache_hit_tokens=20,
                prompt_cache_miss_tokens=80,
                reasoning_tokens=0,
                candidate_format="json_expression",
                provider_fingerprint="current-backend",
            )
            for _ in range(4)
        ]

        contract = canary._derive_current_response_contract(
            generator,
            responses,
            expected_response_model="deepseek-v4-flash",
        )

        self.assertEqual(contract.prompt_cache_mode, "complete")
        self.assertEqual(contract.provider_fingerprint_mode, "exact_sha256")
        self.assertEqual(
            contract.provider_fingerprint_sha256,
            canary._sha256_text("current-backend"),
        )


class FairChoiceCanaryCLITests(unittest.TestCase):
    def test_exclusive_writer_is_mode_0600_and_returns_exact_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifact.json"
            value = {"z": [1, 2], "a": "opaque"}

            observed = canary.write_fair_choice_canary_json_exclusive(
                value, output
            )

            payload = output.read_bytes()
            expected = hashlib.sha256(payload).hexdigest()
            self.assertEqual(observed, expected)
            self.assertEqual(canary.fair_choice_canary_file_sha256(output), expected)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(payload), value)
            with self.assertRaisesRegex(
                canary.FairChoiceCanaryError, "refusing to overwrite"
            ):
                canary.write_fair_choice_canary_json_exclusive(value, output)

    def test_plan_cli_writes_a_valid_plan_and_reports_exact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "plan.json"
            stdout = io.StringIO()

            with redirect_stdout(stdout):
                status = canary.main(["plan", "--output", str(output)])

            self.assertEqual(status, 0)
            saved = json.loads(output.read_bytes())
            canary.validate_fair_choice_canary_plan(saved)
            report = json.loads(stdout.getvalue())
            self.assertEqual(
                report["file_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)

    def test_run_cli_without_execute_makes_zero_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "canary.json"
            with (
                mock.patch.object(canary, "load_provider_credentials") as load,
                mock.patch.object(canary, "build_v3_generator") as build,
                mock.patch.object(canary, "run_fair_choice_canary") as run,
                self.assertRaises(SystemExit),
            ):
                canary.main(
                    [
                        "run",
                        "--plan",
                        str(Path(directory) / "missing-plan.json"),
                        "--route-id",
                        "glm-5.2",
                        "--env-prefix",
                        "TEST",
                        "--output",
                        str(output),
                    ]
                )
            load.assert_not_called()
            build.assert_not_called()
            run.assert_not_called()
            self.assertFalse(output.exists())

    def test_run_cli_refuses_existing_output_before_credentials_or_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.write_text("reserved", encoding="utf-8")
            with (
                mock.patch.object(canary, "load_provider_credentials") as load,
                mock.patch.object(canary, "run_fair_choice_canary") as run,
                self.assertRaisesRegex(
                    canary.FairChoiceCanaryError, "refusing to overwrite"
                ),
            ):
                canary.main(
                    [
                        "run",
                        "--plan",
                        str(Path(directory) / "missing-plan.json"),
                        "--route-id",
                        "glm-5.2",
                        "--env-prefix",
                        "TEST",
                        "--output",
                        str(output),
                        "--execute",
                    ]
                )
            load.assert_not_called()
            run.assert_not_called()

    def test_run_cli_persists_returned_artifact_and_reports_file_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            output = Path(directory) / "route-canary.json"
            plan = canary.build_fair_choice_canary_plan()
            canary.write_fair_choice_canary_json_exclusive(plan, plan_path)
            fake_generator = object()
            fake_artifact = {"kind": "unit-canary", "passed": True}
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    canary, "load_provider_credentials", return_value=object()
                ) as load,
                mock.patch.object(
                    canary, "build_v3_generator", return_value=fake_generator
                ) as build,
                mock.patch.object(
                    canary,
                    "run_fair_choice_canary",
                    return_value=fake_artifact,
                ) as run,
                redirect_stdout(stdout),
            ):
                status = canary.main(
                    [
                        "run",
                        "--plan",
                        str(plan_path),
                        "--route-id",
                        "glm-5.2",
                        "--env-prefix",
                        "TEST",
                        "--output",
                        str(output),
                        "--execute",
                    ]
                )

            self.assertEqual(status, 0)
            load.assert_called_once_with(prefix="TEST", env_file=None)
            build.assert_called_once_with(load.return_value)
            run.assert_called_once_with(plan, "glm-5.2", fake_generator)
            self.assertEqual(json.loads(output.read_bytes()), fake_artifact)
            report = json.loads(stdout.getvalue())
            self.assertEqual(
                report["file_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
