from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.credentials import ProviderCredentials
from src.development_gate import (
    AttemptLedger,
    DEVELOPMENT_GATE_ARMS,
    DEVELOPMENT_GATE_CONFIG_PATH,
    DEVELOPMENT_GATE_EPISODE,
    DEVELOPMENT_GATE_EXPECTED_CALLS,
    DEVELOPMENT_GATE_MODEL,
    DEVELOPMENT_GATE_OFFICIAL_PROVIDER,
    DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
    DEVELOPMENT_GATE_VOLCENGINE_ENDPOINT,
    DEVELOPMENT_GATE_VOLCENGINE_PROVIDER,
    DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL,
    DEVELOPMENT_GATE_WORLD,
    DevelopmentGateError,
    ProgressReportingGeneratorFactory,
    _validate_completed_summary,
    _write_new_json,
    build_live_generator_factory,
    main,
    preflight_development_gate,
    run_development_gate,
    validate_development_gate_config,
)
from src.experiment import GeneratorContext
from src.providers import ResponsePayloadError
from src.runner import CANDIDATE_FORMATS, GenerationResponse


SECRET = "development-gate-unit-test-secret"


def credentials(
    model: str = DEVELOPMENT_GATE_MODEL,
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
    return credentials(base_url=DEVELOPMENT_GATE_VOLCENGINE_ENDPOINT + suffix)


class FakeLiveGenerator:
    def __init__(self, factory: "FakeLiveFactory") -> None:
        self.factory = factory

    def generate(self, prompt: str, **kwargs) -> GenerationResponse:
        self.factory.calls.append({"prompt": prompt, **kwargs})
        if (
            self.factory.fail_after is not None
            and len(self.factory.calls) > self.factory.fail_after
        ):
            raise RuntimeError("synthetic provider failure containing no credential")
        return GenerationResponse(
            expression=self.factory.expression,
            input_tokens=10,
            output_tokens=self.factory.output_tokens,
            latency_ms=3.5,
            provider_request_count=self.factory.provider_request_count,
            seed_supported=False,
            provider_model=self.factory.provider_model,
            provider_fingerprint=self.factory.provider_fingerprint,
            finish_reason="stop",
            prompt_cache_hit_tokens=self.factory.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=self.factory.prompt_cache_miss_tokens,
            reasoning_tokens=self.factory.reasoning_tokens,
            candidate_format=self.factory.candidate_format,
        )


class FakeLiveFactory:
    evidence = True
    evidence_reason = "offline unit-test fake"
    mode = "development-gate-unit-test"

    def __init__(
        self,
        *,
        fail_after: int | None = None,
        provider_request_count: int = 1,
        candidate_format: str | None = "json_expression",
        output_tokens: int = 2,
        prompt_cache_hit_tokens: int | None = 6,
        prompt_cache_miss_tokens: int | None = 4,
        provider_model: str = DEVELOPMENT_GATE_MODEL,
        provider_fingerprint: str | None = "fp-development-gate-unit-test",
        reasoning_tokens: int | None = None,
        expression: object = "(var x1)",
    ) -> None:
        self.calls: list[dict] = []
        self.contexts: list[GeneratorContext] = []
        self.fail_after = fail_after
        self.provider_request_count = provider_request_count
        self.candidate_format = candidate_format
        self.output_tokens = output_tokens
        self.prompt_cache_hit_tokens = prompt_cache_hit_tokens
        self.prompt_cache_miss_tokens = prompt_cache_miss_tokens
        self.provider_model = provider_model
        self.provider_fingerprint = provider_fingerprint
        self.reasoning_tokens = reasoning_tokens
        self.expression = expression

    def __call__(self, context: GeneratorContext) -> FakeLiveGenerator:
        self.contexts.append(context)
        return FakeLiveGenerator(self)


def volcengine_factory(**overrides: object) -> FakeLiveFactory:
    values: dict[str, object] = {
        "provider_model": DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL,
        "provider_fingerprint": None,
        "prompt_cache_hit_tokens": None,
        "prompt_cache_miss_tokens": None,
        "reasoning_tokens": 0,
    }
    values.update(overrides)
    return FakeLiveFactory(**values)  # type: ignore[arg-type]


class DevelopmentGateConfigTests(unittest.TestCase):
    def test_checked_in_config_is_exactly_one_world_seven_arms_and_140_calls(self) -> None:
        config = validate_development_gate_config()

        self.assertEqual(config["worlds"], [DEVELOPMENT_GATE_WORLD])
        self.assertEqual(config["episode"], DEVELOPMENT_GATE_EPISODE)
        self.assertEqual(config["arms"], DEVELOPMENT_GATE_ARMS)
        self.assertEqual(config["model"]["name"], DEVELOPMENT_GATE_MODEL)
        self.assertIsNone(config["model"]["snapshot"])
        planned = (
            len(config["worlds"])
            * len(config["arms"])
            * config["episode"]["rounds"]
            * config["episode"]["candidates_per_round"]
        )
        self.assertEqual(planned, DEVELOPMENT_GATE_EXPECTED_CALLS)

    def test_volcengine_config_changes_only_the_audited_provider_profile(self) -> None:
        official = validate_development_gate_config()
        volcengine = validate_development_gate_config(
            DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH
        )

        self.assertEqual(
            official["model"]["provider"], DEVELOPMENT_GATE_OFFICIAL_PROVIDER
        )
        self.assertEqual(
            volcengine["model"]["provider"], DEVELOPMENT_GATE_VOLCENGINE_PROVIDER
        )
        official_without_provider = copy.deepcopy(official)
        volcengine_without_provider = copy.deepcopy(volcengine)
        del official_without_provider["model"]["provider"]
        del volcengine_without_provider["model"]["provider"]
        self.assertEqual(official_without_provider, volcengine_without_provider)

        unknown = copy.deepcopy(volcengine)
        unknown["model"]["provider"] = "unreviewed-openai-compatible"
        with self.assertRaisesRegex(DevelopmentGateError, "audited provider profile"):
            validate_development_gate_config(unknown)

    def test_preflight_rejects_any_gate_contract_or_credential_model_drift(self) -> None:
        baseline = validate_development_gate_config()
        drifts = []

        world = copy.deepcopy(baseline)
        world["worlds"][0]["seed"] = 1001
        drifts.append(world)

        episode = copy.deepcopy(baseline)
        episode["episode"]["rounds"] = 4
        episode["arms"]["A"]["temperatures"] = [1.2, 0.9, 0.6, 0.2]
        episode["arms"]["C"]["temperatures"] = [1.2, 0.2, 1.2, 0.2]
        drifts.append(episode)

        arms = copy.deepcopy(baseline)
        del arms["arms"]["MTX"]
        drifts.append(arms)

        model = copy.deepcopy(baseline)
        model["model"]["snapshot"] = "movable-alias"
        drifts.append(model)

        extra = copy.deepcopy(baseline)
        extra["unreviewed"] = True
        drifts.append(extra)

        for drift in drifts:
            with self.subTest(drift=drift):
                with self.assertRaises(DevelopmentGateError):
                    preflight_development_gate(credentials(), config=drift)

        with self.assertRaisesRegex(DevelopmentGateError, "credential model"):
            preflight_development_gate(credentials("different-model"))

    def test_volcengine_endpoint_is_bound_after_trailing_slash_normalization(self) -> None:
        validated = preflight_development_gate(
            volcengine_credentials(trailing_slash=True),
            config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
        )
        self.assertEqual(
            validated["model"]["provider"], DEVELOPMENT_GATE_VOLCENGINE_PROVIDER
        )

        with self.assertRaisesRegex(DevelopmentGateError, "Volcengine endpoint"):
            preflight_development_gate(
                credentials(
                    base_url="https://ark.cn-beijing.volces.com/api/v3"
                ),
                config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
            )


class DevelopmentGateProviderTests(unittest.TestCase):
    def test_live_factory_disables_thinking_seed_and_provider_retries(self) -> None:
        delegate = mock.Mock()
        delegate.evidence = True
        delegate.evidence_reason = "declared"
        delegate.mode = "live"
        with mock.patch(
            "src.development_gate.OpenAICompatibleGeneratorFactory",
            return_value=delegate,
        ) as factory_type:
            wrapped = build_live_generator_factory(credentials(), progress_stream=io.StringIO())

        factory_type.assert_called_once_with(
            base_url="https://api.deepseek.example/v1",
            api_key=SECRET,
            model=DEVELOPMENT_GATE_MODEL,
            seed_supported=False,
            evidence=False,
            mode="development-gate-live",
            evidence_reason=mock.ANY,
            timeout=60.0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.assertFalse(wrapped.evidence)
        self.assertEqual(wrapped.expected_responses, DEVELOPMENT_GATE_EXPECTED_CALLS)
        self.assertEqual(
            wrapped.provider_profile, DEVELOPMENT_GATE_OFFICIAL_PROVIDER
        )

    def test_live_factory_uses_request_alias_but_freezes_volcengine_response_alias(self) -> None:
        delegate = mock.Mock()
        delegate.mode = "live"
        with mock.patch(
            "src.development_gate.OpenAICompatibleGeneratorFactory",
            return_value=delegate,
        ) as factory_type:
            wrapped = build_live_generator_factory(
                volcengine_credentials(),
                config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
                progress_stream=io.StringIO(),
            )

        self.assertEqual(
            factory_type.call_args.kwargs["model"], DEVELOPMENT_GATE_MODEL
        )
        self.assertEqual(
            wrapped.provider_profile, DEVELOPMENT_GATE_VOLCENGINE_PROVIDER
        )
        self.assertEqual(
            wrapped._reporter.expected_response_model,
            DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL,
        )

    def test_progress_is_sanitized_and_only_follows_successful_responses(self) -> None:
        stream = io.StringIO()
        delegate = FakeLiveFactory(fail_after=1)
        factory = ProgressReportingGeneratorFactory(
            delegate,
            expected_responses=2,
            stream=stream,
        )
        context = GeneratorContext(
            experiment="test",
            episode={},
            model={},
            max_output_tokens=DEVELOPMENT_GATE_EPISODE["max_output_tokens"],
        )
        generator = factory(context)

        generator.generate("prompt containing private material", temperature=0.2)
        with self.assertRaises(RuntimeError):
            generator.generate("second prompt", temperature=0.2)

        progress = stream.getvalue()
        self.assertEqual(factory.successful_responses, 1)
        self.assertEqual(progress.count("[development-gate] response"), 1)
        self.assertIn("001/002 ok", progress)
        self.assertNotIn("prompt containing private material", progress)
        self.assertNotIn("(var x1)", progress)
        self.assertNotIn(SECRET, progress)

    def test_attempt_ledger_durably_records_success_and_ambiguous_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempt.jsonl"
            ledger = AttemptLedger(
                path,
                provenance={"source_manifest_sha256": "a" * 64},
                config_sha256="b" * 64,
            )
            factory = ProgressReportingGeneratorFactory(
                FakeLiveFactory(fail_after=1),
                expected_responses=2,
                stream=io.StringIO(),
                attempt_ledger=ledger,
            )
            context = GeneratorContext(
                experiment="test",
                episode={},
                model={},
                max_output_tokens=DEVELOPMENT_GATE_EPISODE["max_output_tokens"],
            )
            generator = factory(context)
            generator.generate(
                "first prompt",
                temperature=0.2,
                round_index=0,
                candidate_index=0,
            )
            with self.assertRaises(RuntimeError):
                generator.generate(
                    "second prompt",
                    temperature=0.2,
                    round_index=0,
                    candidate_index=1,
                )
            ledger.close()
            text = path.read_text(encoding="utf-8")
            rows = [json.loads(line) for line in text.splitlines()]
            events = [row["event"] for row in rows]

        self.assertEqual(
            events,
            [
                "attempt_started",
                "logical_request_started",
                "logical_request_succeeded",
                "logical_request_started",
                "logical_request_failed_or_ambiguous",
            ],
        )
        self.assertNotIn(SECRET, text)
        self.assertNotIn("first prompt", text)
        self.assertNotIn("synthetic provider failure containing no credential", text)
        succeeded = next(
            row for row in rows if row["event"] == "logical_request_succeeded"
        )
        self.assertEqual(succeeded["candidate_format"], "json_expression")
        self.assertRegex(succeeded["provider_fingerprint_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("fp-development-gate-unit-test", text)

    def test_attempt_ledger_records_only_closed_provider_failure_categories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempt.jsonl"
            ledger = AttemptLedger(
                path,
                provenance={"source_manifest_sha256": "a" * 64},
                config_sha256="b" * 64,
            )
            reporter = ProgressReportingGeneratorFactory(
                FakeLiveFactory(),
                expected_responses=1,
                stream=io.StringIO(),
                attempt_ledger=ledger,
            )._reporter
            reporter.fail(1, ResponsePayloadError(f"arbitrary payload with {SECRET}"))
            reporter.fail(2, RuntimeError(f"arbitrary failure with {SECRET}"))
            ledger.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]

        self.assertEqual(
            rows[1]["provider_failure_category"], "response_payload_error"
        )
        self.assertNotIn("provider_failure_category", rows[2])
        self.assertNotIn(SECRET, json.dumps(rows))


class DevelopmentGateRunTests(unittest.TestCase):
    def test_complete_offline_injected_run_has_140_calls_and_harness_evidence_gate(self) -> None:
        delegate = FakeLiveFactory()
        progress = io.StringIO()

        summary = run_development_gate(
            credentials(),
            generator_factory=delegate,
            progress_stream=progress,
        )

        self.assertEqual(len(delegate.calls), DEVELOPMENT_GATE_EXPECTED_CALLS)
        self.assertEqual(len(delegate.contexts), len(DEVELOPMENT_GATE_ARMS))
        self.assertEqual(
            summary["budget"]["generation_calls_completed"],
            DEVELOPMENT_GATE_EXPECTED_CALLS,
        )
        self.assertEqual(
            summary["budget"]["provider_requests"],
            DEVELOPMENT_GATE_EXPECTED_CALLS,
        )
        self.assertEqual(summary["budget"]["retry_count"], 0)
        self.assertEqual(
            summary["budget"]["max_output_tokens_planned"],
            DEVELOPMENT_GATE_EXPECTED_CALLS
            * DEVELOPMENT_GATE_EPISODE["max_output_tokens"],
        )
        self.assertEqual(summary["budget"]["max_output_tokens_planned"], 35_840)
        self.assertFalse(summary["evidence"])
        self.assertEqual(summary["evidence_scope"], "non-evidence")
        self.assertEqual(
            summary["model"]["observed_response_models"],
            [DEVELOPMENT_GATE_MODEL],
        )
        candidate_formats = {
            candidate["candidate_format"]
            for run in summary["runs"]
            for candidate in run["candidates"]
        }
        self.assertEqual(candidate_formats, {"json_expression"})
        self.assertLessEqual(candidate_formats, CANDIDATE_FORMATS)
        self.assertEqual(
            summary["model"]["observed_system_fingerprints"],
            ["fp-development-gate-unit-test"],
        )
        lines = progress.getvalue().splitlines()
        self.assertEqual(len(lines), DEVELOPMENT_GATE_EXPECTED_CALLS)
        self.assertIn("001/140 ok", lines[0])
        self.assertIn("140/140 ok", lines[-1])
        self.assertNotIn(SECRET, json.dumps(summary, sort_keys=True))
        self.assertNotIn(SECRET, progress.getvalue())

    def test_volcengine_complete_140_call_profile_and_sanitized_contract(self) -> None:
        delegate = volcengine_factory()
        summary = run_development_gate(
            volcengine_credentials(trailing_slash=True),
            config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
            generator_factory=delegate,
            progress_stream=io.StringIO(),
            provenance_manifest={
                "source_manifest_sha256": "a" * 64,
                "environment": {"git_head": "b" * 40},
                "files": [],
            },
        )

        self.assertEqual(len(delegate.calls), DEVELOPMENT_GATE_EXPECTED_CALLS)
        self.assertEqual(summary["budget"]["generation_calls_completed"], 140)
        self.assertEqual(summary["budget"]["provider_requests"], 140)
        self.assertEqual(summary["budget"]["retry_count"], 0)
        self.assertEqual(summary["budget"]["max_output_tokens_planned"], 35_840)
        self.assertIsNone(summary["budget"]["prompt_cache_hit_tokens"])
        self.assertIsNone(summary["budget"]["prompt_cache_miss_tokens"])
        self.assertEqual(
            summary["model"]["observed_response_models"],
            [DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL],
        )
        self.assertEqual(summary["model"]["observed_system_fingerprints"], [])
        self.assertFalse(summary["evidence"])
        contract = summary["provider_contract"]
        self.assertEqual(contract["profile"], DEVELOPMENT_GATE_VOLCENGINE_PROVIDER)
        self.assertEqual(contract["request_model_alias"], DEVELOPMENT_GATE_MODEL)
        self.assertEqual(
            contract["expected_response_model_alias"],
            DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL,
        )
        self.assertIn(
            "unavailable_after_adapter_normalization",
            contract["prompt_cache_usage"]["capability"],
        )
        self.assertIn(
            "null_or_omitted_after_adapter_normalization",
            contract["prompt_cache_usage"]["requirement"],
        )
        self.assertIn(
            "unavailable_after_adapter_normalization",
            contract["system_fingerprint"]["capability"],
        )
        self.assertIn(
            "null_or_omitted_after_adapter_normalization",
            contract["system_fingerprint"]["requirement"],
        )
        self.assertTrue(contract["endpoint"]["contract_satisfied"])
        self.assertTrue(contract["contract_satisfied"])
        self.assertEqual(
            contract["endpoint"]["normalized_url_sha256"],
            hashlib.sha256(
                DEVELOPMENT_GATE_VOLCENGINE_ENDPOINT.encode("utf-8")
            ).hexdigest(),
        )
        serialized = json.dumps(summary, sort_keys=True)
        self.assertNotIn(DEVELOPMENT_GATE_VOLCENGINE_ENDPOINT, serialized)
        self.assertNotIn(SECRET, serialized)

    def test_volcengine_wrong_endpoint_is_rejected_before_fake_generator_call(self) -> None:
        delegate = volcengine_factory()
        with self.assertRaisesRegex(DevelopmentGateError, "Volcengine endpoint"):
            run_development_gate(
                credentials(
                    base_url="https://ark.cn-beijing.volces.com/api/v3"
                ),
                config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
                generator_factory=delegate,
                progress_stream=io.StringIO(),
            )
        self.assertEqual(delegate.calls, [])

    def test_volcengine_alias_cache_and_fingerprint_drift_abort_immediately(self) -> None:
        cases = {
            "response_alias": {
                "provider_model": DEVELOPMENT_GATE_MODEL,
            },
            "cache_partial": {
                "prompt_cache_hit_tokens": 0,
            },
            "cache_both_present": {
                "prompt_cache_hit_tokens": 6,
                "prompt_cache_miss_tokens": 4,
            },
            "fingerprint_present": {
                "provider_fingerprint": "unexpected-fingerprint",
            },
        }
        for name, overrides in cases.items():
            with self.subTest(name=name):
                delegate = volcengine_factory(**overrides)
                with self.assertRaisesRegex(DevelopmentGateError, "per-call contract"):
                    run_development_gate(
                        volcengine_credentials(),
                        config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
                        generator_factory=delegate,
                        progress_stream=io.StringIO(),
                    )
                self.assertEqual(len(delegate.calls), 1)

    def test_candidate_dsl_failure_consumes_samples_without_contract_fail_fast(self) -> None:
        delegate = volcengine_factory(
            candidate_format="invalid_json",
            expression="not valid dsl",
        )
        summary = run_development_gate(
            volcengine_credentials(),
            config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
            generator_factory=delegate,
            progress_stream=io.StringIO(),
        )

        self.assertEqual(len(delegate.calls), DEVELOPMENT_GATE_EXPECTED_CALLS)
        self.assertEqual(summary["budget"]["generation_calls_completed"], 140)
        self.assertTrue(
            all(
                candidate["syntax_valid"] is False
                for run in summary["runs"]
                for candidate in run["candidates"]
            )
        )

    def test_volcengine_postflight_rejects_profile_metadata_or_budget_drift(self) -> None:
        summary = run_development_gate(
            volcengine_credentials(),
            config=DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH,
            generator_factory=volcengine_factory(),
            progress_stream=io.StringIO(),
        )
        stats = ProgressReportingGeneratorFactory(
            volcengine_factory(),
            provider_profile=DEVELOPMENT_GATE_VOLCENGINE_PROVIDER,
            provider_contract_metadata=summary["provider_contract"],
            stream=io.StringIO(),
        )
        stats._reporter.started = DEVELOPMENT_GATE_EXPECTED_CALLS
        stats._reporter.count = DEVELOPMENT_GATE_EXPECTED_CALLS

        metadata_drift = copy.deepcopy(summary)
        metadata_drift["provider_contract"]["contract_satisfied"] = False
        with self.assertRaisesRegex(DevelopmentGateError, "provider-contract metadata"):
            _validate_completed_summary(metadata_drift, stats)

        budget_drift = copy.deepcopy(summary)
        budget_drift["budget"]["prompt_cache_hit_tokens"] = 0
        with self.assertRaisesRegex(DevelopmentGateError, "cache budgets"):
            _validate_completed_summary(budget_drift, stats)

    def test_postflight_rejects_a_response_ledger_that_claims_retries(self) -> None:
        delegate = FakeLiveFactory(provider_request_count=2)
        with self.assertRaisesRegex(DevelopmentGateError, "per-call contract"):
            run_development_gate(
                credentials(),
                generator_factory=delegate,
                progress_stream=io.StringIO(),
            )

    def test_postflight_requires_a_closed_format_for_all_140_candidates(self) -> None:
        delegate = FakeLiveFactory(candidate_format=None)
        with self.assertRaisesRegex(DevelopmentGateError, "per-call contract"):
            run_development_gate(
                credentials(),
                generator_factory=delegate,
                progress_stream=io.StringIO(),
            )

    def test_postflight_requires_exact_cache_accounting_and_output_cap(self) -> None:
        cases = (
            FakeLiveFactory(prompt_cache_miss_tokens=None),
            FakeLiveFactory(prompt_cache_miss_tokens=3),
            FakeLiveFactory(output_tokens=257),
        )
        for delegate in cases:
            with self.subTest(delegate=delegate), self.assertRaises(
                DevelopmentGateError
            ):
                run_development_gate(
                    credentials(),
                    generator_factory=delegate,
                    progress_stream=io.StringIO(),
                )
            self.assertEqual(len(delegate.calls), 1)

    def test_postflight_rejects_output_budget_metadata_drift(self) -> None:
        delegate = FakeLiveFactory()
        summary = run_development_gate(
            credentials(),
            generator_factory=delegate,
            progress_stream=io.StringIO(),
        )
        summary["budget"]["max_output_tokens_planned"] -= 1

        complete_stats = mock.Mock(
            started_requests=DEVELOPMENT_GATE_EXPECTED_CALLS,
            successful_responses=DEVELOPMENT_GATE_EXPECTED_CALLS,
            expected_responses=DEVELOPMENT_GATE_EXPECTED_CALLS,
        )
        with self.assertRaises(DevelopmentGateError):
            _validate_completed_summary(summary, complete_stats)


class DevelopmentGateArtifactAndCliTests(unittest.TestCase):
    def test_artifact_is_secret_checked_and_exclusively_created(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "gate.json"
            _write_new_json(output, {"safe": True}, forbidden_values=(SECRET,))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"safe": True})
            with self.assertRaises(FileExistsError):
                _write_new_json(output, {"safe": False}, forbidden_values=(SECRET,))

            secret_output = Path(directory) / "secret.json"
            with self.assertRaisesRegex(DevelopmentGateError, "credential"):
                _write_new_json(
                    secret_output,
                    {"accidental": SECRET},
                    forbidden_values=(SECRET,),
                )
            self.assertFalse(secret_output.exists())

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
        self.assertIn("checkpoint/resume is not implemented safely", stderr.getvalue())

    def test_cli_checks_output_before_loading_credentials_or_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.write_text("untouched", encoding="utf-8")
            stderr = io.StringIO()
            with (
                mock.patch("src.development_gate.load_provider_credentials") as load,
                mock.patch("src.development_gate.run_development_gate") as run,
                contextlib.redirect_stderr(stderr),
            ):
                status = main(
                    [
                        "--env-file",
                        "credentials.env",
                        "--output",
                        str(output),
                        "--attempt-ledger",
                        str(Path(directory) / "attempt.jsonl"),
                        "--execute",
                    ]
                )

            self.assertEqual(status, 1)
            load.assert_not_called()
            run.assert_not_called()
            self.assertEqual(output.read_text(encoding="utf-8"), "untouched")
            self.assertNotIn(SECRET, stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
