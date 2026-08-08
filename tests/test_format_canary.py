from __future__ import annotations

import contextlib
import copy
import hashlib
import inspect
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.credentials import ProviderCredentials
from src.format_canary import (
    FORMAT_CANARY_ARM,
    FORMAT_CANARY_EPISODE,
    FORMAT_CANARY_EXPECTED_CALLS,
    FORMAT_CANARY_MODEL,
    FORMAT_CANARY_OFFICIAL_PROVIDER,
    FORMAT_CANARY_TEMPERATURES,
    FORMAT_CANARY_VOLCENGINE_CONFIG_PATH,
    FORMAT_CANARY_VOLCENGINE_ENDPOINT,
    FORMAT_CANARY_VOLCENGINE_PROVIDER,
    FORMAT_CANARY_VOLCENGINE_RESPONSE_MODEL,
    FORMAT_CANARY_WORLD,
    FormatCanaryAttemptLedger,
    FormatCanaryError,
    _write_new_json,
    build_live_generator,
    main,
    preflight_format_canary,
    run_format_canary,
    validate_format_canary_config,
)
from src.providers import UsagePayloadError
from src.runner import GenerationResponse
from src.verifier import Verifier


SECRET = "format-canary-unit-test-secret"
FINGERPRINT = "provider-fingerprint-unit-test"
ARCHIVE_HEADER = "Previously explored candidates"


def credentials(
    model: str = FORMAT_CANARY_MODEL,
    *,
    base_url: str = "https://api.deepseek.example/v1",
) -> ProviderCredentials:
    return ProviderCredentials(
        base_url=base_url,
        model=model,
        api_key=SECRET,
    )


def volcengine_credentials(*, trailing_slash: bool = False) -> ProviderCredentials:
    suffix = "/" if trailing_slash else ""
    return credentials(base_url=FORMAT_CANARY_VOLCENGINE_ENDPOINT + suffix)


def generation_response(**overrides: object) -> GenerationResponse:
    values: dict[str, object] = {
        "expression": "(var x1)",
        "input_tokens": 10,
        "output_tokens": 2,
        "latency_ms": 3.5,
        "provider_request_count": 1,
        "seed_supported": False,
        "provider_model": FORMAT_CANARY_MODEL,
        "finish_reason": "stop",
        "prompt_cache_hit_tokens": 6,
        "prompt_cache_miss_tokens": 4,
        "reasoning_tokens": 0,
        "candidate_format": "json_expression",
        "provider_fingerprint": FINGERPRINT,
    }
    values.update(overrides)
    parameters = inspect.signature(GenerationResponse).parameters
    constructor_values = {
        key: value for key, value in values.items() if key in parameters
    }
    response = GenerationResponse(**constructor_values)  # type: ignore[arg-type]
    # Keep these tests compatible while provider_fingerprint is introduced in
    # the shared response envelope. The canary intentionally reads it via
    # getattr so it does not depend on runner-side serialization.
    if "provider_fingerprint" not in parameters:
        object.__setattr__(
            response,
            "provider_fingerprint",
            values["provider_fingerprint"],
        )
    return response


class FakeGenerator:
    def __init__(
        self,
        *,
        default_overrides: dict[str, object] | None = None,
        overrides: dict[int, dict[str, object]] | None = None,
        failure_index: int | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.default_overrides = default_overrides or {}
        self.overrides = overrides or {}
        self.failure_index = failure_index

    def generate(self, prompt: str, **kwargs: object) -> GenerationResponse:
        index = len(self.calls)
        self.calls.append({"prompt": prompt, **kwargs})
        if index == self.failure_index:
            raise UsagePayloadError(f"unsafe provider detail containing {SECRET}")
        response_overrides = {
            **self.default_overrides,
            **self.overrides.get(index, {}),
        }
        return generation_response(**response_overrides)


def volcengine_generator(
    *,
    overrides: dict[int, dict[str, object]] | None = None,
) -> FakeGenerator:
    return FakeGenerator(
        default_overrides={
            "provider_model": FORMAT_CANARY_VOLCENGINE_RESPONSE_MODEL,
            "prompt_cache_hit_tokens": None,
            "prompt_cache_miss_tokens": None,
            "provider_fingerprint": None,
        },
        overrides=overrides,
    )


def provenance(hash_character: str = "a") -> dict[str, object]:
    return {
        "source_manifest_sha256": hash_character * 64,
        "environment": {"git_head": "b" * 40},
        "files": [],
    }


class FormatCanaryConfigTests(unittest.TestCase):
    def test_checked_in_config_freezes_exact_two_round_mtx_budget(self) -> None:
        config = validate_format_canary_config()

        self.assertEqual(config["worlds"], [FORMAT_CANARY_WORLD])
        self.assertEqual(config["episode"], FORMAT_CANARY_EPISODE)
        self.assertEqual(config["arms"], FORMAT_CANARY_ARM)
        self.assertEqual(config["model"]["name"], FORMAT_CANARY_MODEL)
        self.assertIsNone(config["model"]["snapshot"])
        planned = (
            len(config["worlds"])
            * len(config["arms"])
            * config["episode"]["rounds"]
            * config["episode"]["candidates_per_round"]
        )
        self.assertEqual(planned, FORMAT_CANARY_EXPECTED_CALLS)

    def test_volcengine_config_changes_only_the_audited_provider_profile(self) -> None:
        official = validate_format_canary_config()
        volcengine = validate_format_canary_config(
            FORMAT_CANARY_VOLCENGINE_CONFIG_PATH
        )

        self.assertEqual(
            volcengine["model"]["provider"],
            FORMAT_CANARY_VOLCENGINE_PROVIDER,
        )
        self.assertEqual(official["model"]["provider"], FORMAT_CANARY_OFFICIAL_PROVIDER)
        official_without_provider = copy.deepcopy(official)
        volcengine_without_provider = copy.deepcopy(volcengine)
        del official_without_provider["model"]["provider"]
        del volcengine_without_provider["model"]["provider"]
        self.assertEqual(official_without_provider, volcengine_without_provider)

        unknown = copy.deepcopy(volcengine)
        unknown["model"]["provider"] = "unreviewed-openai-compatible"
        with self.assertRaisesRegex(FormatCanaryError, "audited provider profile"):
            validate_format_canary_config(unknown)

    def test_preflight_rejects_protocol_or_credential_model_drift(self) -> None:
        baseline = validate_format_canary_config()
        drifts: list[dict] = []

        world = copy.deepcopy(baseline)
        world["worlds"][0]["seed"] = 1001
        drifts.append(world)

        episode = copy.deepcopy(baseline)
        episode["episode"]["rounds"] = 3
        drifts.append(episode)

        arm = copy.deepcopy(baseline)
        arm["arms"]["MTX"]["temperatures"] = [0.2, 0.7, 1.2, 1.2]
        drifts.append(arm)

        model = copy.deepcopy(baseline)
        model["model"]["snapshot"] = "unreviewed"
        drifts.append(model)

        for drift in drifts:
            with self.subTest(drift=drift):
                with self.assertRaises((FormatCanaryError, ValueError)):
                    preflight_format_canary(credentials(), config=drift)

        with self.assertRaisesRegex(FormatCanaryError, "credential model"):
            preflight_format_canary(credentials("different-model"))


class FormatCanaryProviderTests(unittest.TestCase):
    def test_live_adapter_disables_thinking_and_provider_seed(self) -> None:
        with mock.patch("src.format_canary.OpenAICompatibleGenerator") as generator_type:
            result = build_live_generator(credentials())

        self.assertIs(result, generator_type.return_value)
        generator_type.assert_called_once_with(
            base_url="https://api.deepseek.example/v1",
            api_key=SECRET,
            model=FORMAT_CANARY_MODEL,
            seed_supported=False,
            timeout=60.0,
            extra_body={"thinking": {"type": "disabled"}},
        )

    def test_success_path_runs_eight_exact_slots_and_never_evaluates_test(self) -> None:
        generator = FakeGenerator()
        progress = io.StringIO()
        with mock.patch.object(
            Verifier,
            "verify_test",
            side_effect=AssertionError("private test must not be evaluated"),
        ) as verify_test:
            artifact = run_format_canary(
                credentials(),
                generator=generator,
                progress_stream=progress,
            )

        verify_test.assert_not_called()
        self.assertTrue(artifact["passed"])
        self.assertFalse(artifact["evidence"])
        self.assertEqual(artifact["evidence_scope"], "non-evidence")
        self.assertFalse(artifact["protocol"]["private_test_evaluated"])
        self.assertEqual(len(generator.calls), FORMAT_CANARY_EXPECTED_CALLS)
        self.assertEqual(
            [call["temperature"] for call in generator.calls],
            list(FORMAT_CANARY_TEMPERATURES) * 2,
        )
        self.assertTrue(
            all(
                call["max_output_tokens"]
                == FORMAT_CANARY_EPISODE["max_output_tokens"]
                for call in generator.calls
            )
        )
        self.assertTrue(all(call["seed"] is None for call in generator.calls))
        self.assertTrue(
            all(ARCHIVE_HEADER not in str(call["prompt"]) for call in generator.calls[:4])
        )
        self.assertTrue(
            all(ARCHIVE_HEADER in str(call["prompt"]) for call in generator.calls[4:])
        )
        self.assertEqual(artifact["budget"]["provider_requests"], 8)
        self.assertEqual(artifact["budget"]["retry_count"], 0)
        self.assertEqual(artifact["budget"]["actual_input_tokens"], 80)
        self.assertEqual(artifact["provider"]["fingerprint_value_count"], 1)
        self.assertTrue(
            artifact["criteria"]["cache_accounting_complete_for_all_calls"]
        )
        self.assertTrue(
            artifact["criteria"]["provider_fingerprint_present_and_stable"]
        )
        self.assertTrue(artifact["rounds"][0]["archive_context_absent_for_all_calls"])
        self.assertTrue(artifact["rounds"][1]["archive_context_present_for_all_calls"])
        self.assertTrue(artifact["criteria"]["request_and_response_output_caps_exact"])
        self.assertTrue(artifact["criteria"]["round_candidate_slot_set_exact"])
        self.assertEqual(
            {
                (call["round_index"], call["candidate_index"])
                for round_summary in artifact["rounds"]
                for call in round_summary["calls"]
            },
            {(round_index, slot) for round_index in range(2) for slot in range(4)},
        )
        self.assertNotIn(FINGERPRINT, json.dumps(artifact, sort_keys=True))
        self.assertNotIn(SECRET, json.dumps(artifact, sort_keys=True))
        self.assertNotIn("(var x1)", json.dumps(artifact, sort_keys=True))
        self.assertNotIn("Observed training examples", json.dumps(artifact, sort_keys=True))
        self.assertEqual(len(progress.getvalue().splitlines()), 8)

    def test_all_pass_metadata_and_cache_conditions_are_enforced(self) -> None:
        cases = {
            "model": {0: {"provider_model": "different-model"}},
            "finish": {0: {"finish_reason": "length"}},
            "request_count": {0: {"provider_request_count": 2}},
            "reasoning": {0: {"reasoning_tokens": 1}},
            "output_cap": {0: {"output_tokens": 257}},
            "cache_missing": {0: {"prompt_cache_miss_tokens": None}},
            "cache_mismatch": {0: {"prompt_cache_miss_tokens": 3}},
            "fingerprint_missing": {0: {"provider_fingerprint": None}},
            "fingerprint_unstable": {7: {"provider_fingerprint": "second-fingerprint"}},
            "seed_flag": {0: {"seed_supported": True}},
        }
        for name, overrides in cases.items():
            with self.subTest(name=name):
                artifact = run_format_canary(
                    credentials(),
                    generator=FakeGenerator(overrides=overrides),
                    progress_stream=io.StringIO(),
                )
                self.assertFalse(artifact["passed"])
                self.assertEqual(artifact["budget"]["logical_calls_succeeded"], 8)

    def test_volcengine_profile_accepts_only_its_frozen_capability_contract(self) -> None:
        artifact = run_format_canary(
            volcengine_credentials(trailing_slash=True),
            config=FORMAT_CANARY_VOLCENGINE_CONFIG_PATH,
            generator=volcengine_generator(),
            progress_stream=io.StringIO(),
        )

        self.assertTrue(artifact["passed"])
        self.assertEqual(
            artifact["provider"]["provider"],
            FORMAT_CANARY_VOLCENGINE_PROVIDER,
        )
        self.assertEqual(artifact["provider"]["request_model"], FORMAT_CANARY_MODEL)
        self.assertEqual(
            artifact["provider"]["expected_response_model"],
            FORMAT_CANARY_VOLCENGINE_RESPONSE_MODEL,
        )
        capability = artifact["provider"]["capability_contract"]
        self.assertEqual(
            capability["scope"],
            "provider_telemetry_only_not_candidate_content",
        )
        self.assertEqual(
            capability["prompt_cache_usage"]["observed_capability"],
            "hit_and_miss_token_counts_unavailable_after_adapter_normalization",
        )
        self.assertEqual(
            capability["prompt_cache_usage"]["pass_requirement"],
            (
                "both_fields_null_or_omitted_after_adapter_normalization_"
                "for_every_call"
            ),
        )
        self.assertEqual(
            capability["system_fingerprint"]["observed_capability"],
            "system_fingerprint_unavailable_after_adapter_normalization",
        )
        self.assertEqual(
            capability["system_fingerprint"]["pass_requirement"],
            "null_or_omitted_after_adapter_normalization_for_every_call",
        )
        endpoint_contract = artifact["provider"]["endpoint_contract"]
        self.assertTrue(endpoint_contract["contract_satisfied"])
        self.assertEqual(
            endpoint_contract["normalized_url_sha256"],
            hashlib.sha256(
                FORMAT_CANARY_VOLCENGINE_ENDPOINT.encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(artifact["provider"]["fingerprint_value_count"], 0)
        self.assertFalse(
            artifact["criteria"]["cache_accounting_complete_for_all_calls"]
        )
        self.assertFalse(
            artifact["criteria"]["provider_fingerprint_present_and_stable"]
        )
        self.assertEqual(
            artifact["protocol"]["candidate_content_gate"]["scope"],
            "generated_candidate_content",
        )
        self.assertIn(
            "all_8_calls",
            artifact["protocol"]["candidate_content_gate"]["requirement"],
        )
        self.assertTrue(
            artifact["criteria"]["prompt_cache_contract_satisfied_for_all_calls"]
        )
        self.assertTrue(
            artifact["criteria"][
                "system_fingerprint_contract_satisfied_for_all_calls"
            ]
        )
        criteria_contract = artifact["criteria_contract"]
        self.assertEqual(
            criteria_contract["aggregation"], "all_required_criteria_true"
        )
        required = criteria_contract["required_criterion_names"]
        self.assertIn("prompt_cache_contract_satisfied_for_all_calls", required)
        self.assertIn(
            "system_fingerprint_contract_satisfied_for_all_calls", required
        )
        self.assertNotIn("cache_accounting_complete_for_all_calls", required)
        self.assertNotIn("provider_fingerprint_present_and_stable", required)
        self.assertTrue(all(artifact["criteria"][name] for name in required))
        for round_summary in artifact["rounds"]:
            self.assertEqual(
                round_summary["json_expression_syntax_runtime_valid_calls"], 4
            )
            self.assertTrue(
                all(
                    call["provider_fingerprint_unavailable"] is True
                    for call in round_summary["calls"]
                )
            )
        serialized = json.dumps(artifact, sort_keys=True)
        self.assertNotIn(FORMAT_CANARY_VOLCENGINE_ENDPOINT, serialized)
        self.assertNotIn(SECRET, serialized)

    def test_volcengine_wrong_endpoint_is_rejected_before_any_paid_call(self) -> None:
        generator = volcengine_generator()

        with self.assertRaisesRegex(FormatCanaryError, "Volcengine endpoint"):
            run_format_canary(
                credentials(
                    base_url="https://ark.cn-beijing.volces.com/api/v3"
                ),
                config=FORMAT_CANARY_VOLCENGINE_CONFIG_PATH,
                generator=generator,
                progress_stream=io.StringIO(),
            )

        self.assertEqual(generator.calls, [])

    def test_volcengine_model_alias_cache_or_fingerprint_drift_fails(self) -> None:
        cases = {
            "response_alias": (
                {0: {"provider_model": FORMAT_CANARY_MODEL}},
                "configured_model_exact_for_all_calls",
            ),
            "cache_both_appear": (
                {
                    0: {
                        "prompt_cache_hit_tokens": 6,
                        "prompt_cache_miss_tokens": 4,
                    }
                },
                "prompt_cache_contract_satisfied_for_all_calls",
            ),
            "cache_partially_appears": (
                {0: {"prompt_cache_hit_tokens": 0}},
                "prompt_cache_contract_satisfied_for_all_calls",
            ),
            "fingerprint_appears_on_one_call": (
                {0: {"provider_fingerprint": FINGERPRINT}},
                "system_fingerprint_contract_satisfied_for_all_calls",
            ),
        }
        for name, (overrides, failed_criterion) in cases.items():
            with self.subTest(name=name):
                artifact = run_format_canary(
                    volcengine_credentials(),
                    config=FORMAT_CANARY_VOLCENGINE_CONFIG_PATH,
                    generator=volcengine_generator(overrides=overrides),
                    progress_stream=io.StringIO(),
                )

                self.assertFalse(artifact["passed"])
                self.assertEqual(artifact["budget"]["logical_calls_succeeded"], 8)
                self.assertFalse(artifact["criteria"][failed_criterion])
                self.assertIn(
                    failed_criterion,
                    artifact["criteria_contract"]["required_criterion_names"],
                )

    def test_amended_cap_accepts_usage_above_the_historical_128_limit(self) -> None:
        artifact = run_format_canary(
            credentials(),
            generator=FakeGenerator(overrides={0: {"output_tokens": 129}}),
            progress_stream=io.StringIO(),
        )

        self.assertTrue(artifact["passed"])
        self.assertEqual(artifact["protocol"]["max_output_tokens"], 256)
        self.assertTrue(artifact["criteria"]["request_and_response_output_caps_exact"])

    def test_round_one_archive_header_must_be_absent_not_merely_ignored(self) -> None:
        # This marker occurs in every prompt, simulating an accidental R1
        # archive section without serializing any prompt into the artifact.
        with mock.patch(
            "src.format_canary._ARCHIVE_PROMPT_HEADER",
            "Observed training examples",
        ):
            artifact = run_format_canary(
                credentials(),
                generator=FakeGenerator(),
                progress_stream=io.StringIO(),
            )

        self.assertFalse(artifact["passed"])
        self.assertFalse(artifact["rounds"][0]["archive_context_exact"])
        self.assertFalse(
            artifact["criteria"]["round_1_four_of_four_json_expression_and_valid"]
        )

    def test_candidate_format_failure_consumes_slot_and_writes_no_raw_material(self) -> None:
        generator = FakeGenerator(
            overrides={
                4: {
                    "expression": SECRET,
                    "candidate_format": "non_string_expression",
                },
                5: {
                    "expression": SECRET,
                    "candidate_format": "extra_fields",
                },
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "attempt.jsonl"
            ledger = FormatCanaryAttemptLedger(
                ledger_path,
                provenance=provenance(),
                config_sha256="c" * 64,
            )
            artifact = run_format_canary(
                credentials(),
                generator=generator,
                progress_stream=io.StringIO(),
                attempt_ledger=ledger,
            )
            ledger.close()
            ledger_text = ledger_path.read_text(encoding="utf-8")
            rows = [json.loads(line) for line in ledger_text.splitlines()]

        self.assertFalse(artifact["passed"])
        self.assertEqual(len(generator.calls), 8)
        self.assertEqual(artifact["budget"]["logical_calls_succeeded"], 8)
        self.assertEqual(
            artifact["rounds"][1]["json_expression_syntax_runtime_valid_calls"],
            2,
        )
        events = [row["event"] for row in rows]
        self.assertEqual(events.count("logical_request_started"), 8)
        self.assertEqual(events.count("logical_request_succeeded"), 8)
        self.assertNotIn("logical_request_failed_or_ambiguous", events)
        self.assertIn("non_string_expression", ledger_text)
        self.assertIn("extra_fields", ledger_text)
        for forbidden in (
            SECRET,
            FINGERPRINT,
            "Observed training examples",
            "(var x1)",
        ):
            self.assertNotIn(forbidden, ledger_text)
            self.assertNotIn(forbidden, json.dumps(artifact, sort_keys=True))

    def test_outer_or_usage_failure_aborts_without_retry_and_is_safely_ledgered(self) -> None:
        generator = FakeGenerator(failure_index=2)
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "attempt.jsonl"
            ledger = FormatCanaryAttemptLedger(
                ledger_path,
                provenance=provenance(),
                config_sha256="c" * 64,
            )
            with self.assertRaises(UsagePayloadError):
                run_format_canary(
                    credentials(),
                    generator=generator,
                    progress_stream=io.StringIO(),
                    attempt_ledger=ledger,
                )
            ledger.close()
            ledger_text = ledger_path.read_text(encoding="utf-8")
            rows = [json.loads(line) for line in ledger_text.splitlines()]

        self.assertEqual(len(generator.calls), 3)
        self.assertEqual(
            [row["event"] for row in rows].count("logical_request_succeeded"),
            2,
        )
        failed = rows[-1]
        self.assertEqual(failed["event"], "logical_request_failed_or_ambiguous")
        self.assertEqual(failed["provider_failure_category"], "usage_payload_error")
        self.assertNotIn(SECRET, ledger_text)
        self.assertNotIn("unsafe provider detail", ledger_text)


class FormatCanaryArtifactAndCliTests(unittest.TestCase):
    def test_writer_is_secret_checked_exclusive_and_refuses_raw_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            _write_new_json(output, {"safe": True}, forbidden_values=(SECRET,))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"safe": True})
            with self.assertRaises(FileExistsError):
                _write_new_json(output, {"safe": False})

            for index, unsafe in enumerate(
                (
                    {"api_key": SECRET},
                    {"prompt": "raw prompt"},
                    {"nested": {"content": "raw content"}},
                    {"safe_name": SECRET},
                )
            ):
                path = Path(directory) / f"unsafe-{index}.json"
                with self.subTest(unsafe=unsafe), self.assertRaises(FormatCanaryError):
                    _write_new_json(path, unsafe, forbidden_values=(SECRET,))
                self.assertFalse(path.exists())

    def test_cli_requires_execute_and_explicitly_rejects_resume(self) -> None:
        base = [
            "--env-file",
            "credentials.env",
            "--output",
            "result.json",
            "--attempt-ledger",
            "attempt.jsonl",
        ]
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as no_exec:
            main(base)
        self.assertEqual(no_exec.exception.code, 2)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as resume:
            main([*base, "--execute", "--resume"])
        self.assertEqual(resume.exception.code, 2)
        self.assertIn("checkpoint/resume is not implemented", stderr.getvalue())

    def test_cli_checks_distinct_unused_paths_before_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "existing.json"
            existing.write_text("untouched", encoding="utf-8")
            ledger = Path(directory) / "attempt.jsonl"
            with (
                mock.patch("src.format_canary.load_provider_credentials") as load,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = main(
                    [
                        "--env-file",
                        "credentials.env",
                        "--output",
                        str(existing),
                        "--attempt-ledger",
                        str(ledger),
                        "--execute",
                    ]
                )
            self.assertEqual(status, 1)
            load.assert_not_called()
            self.assertEqual(existing.read_text(encoding="utf-8"), "untouched")

            same = Path(directory) / "same-path"
            with (
                mock.patch("src.format_canary.load_provider_credentials") as load,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = main(
                    [
                        "--env-file",
                        "credentials.env",
                        "--output",
                        str(same),
                        "--attempt-ledger",
                        str(same),
                        "--execute",
                    ]
                )
            self.assertEqual(status, 1)
            load.assert_not_called()

    def test_source_manifest_drift_aborts_and_writes_no_result_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            ledger = Path(directory) / "attempt.jsonl"
            before = provenance("a")
            after = provenance("d")
            stderr = io.StringIO()
            with (
                mock.patch(
                    "src.format_canary.load_provider_credentials",
                    return_value=credentials(),
                ),
                mock.patch(
                    "src.format_canary.source_manifest",
                    side_effect=[before, after],
                ),
                mock.patch(
                    "src.format_canary.run_format_canary",
                    return_value={"passed": True},
                ),
                contextlib.redirect_stderr(stderr),
            ):
                status = main(
                    [
                        "--env-file",
                        "credentials.env",
                        "--output",
                        str(output),
                        "--attempt-ledger",
                        str(ledger),
                        "--execute",
                    ]
                )

            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual(status, 1)
        self.assertFalse(output.exists())
        self.assertEqual(rows[-1]["event"], "attempt_aborted")
        self.assertFalse(rows[-1]["result_artifact_written"])
        self.assertNotIn(SECRET, stderr.getvalue())

    def test_completed_failed_canary_writes_artifact_then_returns_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            ledger = Path(directory) / "attempt.jsonl"
            manifest = provenance("a")
            completed = {
                "schema_version": 1,
                "kind": "live-format-contract-canary",
                "evidence": False,
                "evidence_scope": "non-evidence",
                "passed": False,
            }
            with (
                mock.patch(
                    "src.format_canary.load_provider_credentials",
                    return_value=credentials(),
                ),
                mock.patch(
                    "src.format_canary.source_manifest",
                    side_effect=[manifest, manifest],
                ),
                mock.patch(
                    "src.format_canary.run_format_canary",
                    return_value=completed,
                ),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = main(
                    [
                        "--env-file",
                        "credentials.env",
                        "--output",
                        str(output),
                        "--attempt-ledger",
                        str(ledger),
                        "--execute",
                    ]
                )

            artifact = json.loads(output.read_text(encoding="utf-8"))
            rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual(status, 1)
        self.assertFalse(artifact["passed"])
        self.assertEqual(rows[-1]["event"], "attempt_completed")
        self.assertFalse(rows[-1]["passed"])


if __name__ == "__main__":
    unittest.main()
