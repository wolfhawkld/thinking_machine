from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.credentials import ProviderCredentials
from src.development_pilot import (
    AttemptLedger,
    DEVELOPMENT_PILOT_ARMS,
    DEVELOPMENT_PILOT_CALLS_PER_RUN,
    DEVELOPMENT_PILOT_CONFIG_PATH,
    DEVELOPMENT_PILOT_EPISODE,
    DEVELOPMENT_PILOT_EXPECTED_CALLS,
    DEVELOPMENT_PILOT_EXPECTED_RUNS,
    DEVELOPMENT_PILOT_MODE,
    DEVELOPMENT_PILOT_MODEL,
    DEVELOPMENT_PILOT_OFFICIAL_PROVIDER,
    DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH,
    DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
    DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER,
    DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL,
    DEVELOPMENT_PILOT_WORLDS,
    DevelopmentPilotError,
    ProgressReportingGeneratorFactory,
    _request_coordinates,
    _validate_completed_summary,
    _write_new_json,
    build_live_generator_factory,
    main,
    preflight_development_pilot,
    run_development_pilot,
    validate_development_pilot_config,
)
from src.experiment import GeneratorContext, _arm_execution_order
import src.experiment as experiment_module
from src.providers import ResponsePayloadError
from src.runner import GenerationResponse


SECRET = "development-pilot-unit-test-secret"
PROVENANCE = {
    "schema_version": 1,
    "created_at_utc": "2026-08-08T00:00:00+00:00",
    "source_manifest_sha256": "a" * 64,
    "files": [],
    "environment": {"git_head": "b" * 40},
}


def credentials(
    model: str = DEVELOPMENT_PILOT_MODEL,
    *,
    base_url: str = "https://api.deepseek.example/v1",
) -> ProviderCredentials:
    return ProviderCredentials(
        base_url=base_url,
        model=model,
        api_key=SECRET,
    )


class FakeLiveGenerator:
    def __init__(self, factory: "FakeLiveFactory") -> None:
        self.factory = factory

    def generate(self, prompt: str, **kwargs) -> GenerationResponse:
        call_index = len(self.factory.calls)
        self.factory.calls.append({"prompt": prompt, **kwargs})
        if self.factory.fail_after is not None and call_index >= self.factory.fail_after:
            raise ResponsePayloadError(f"malformed assistant content containing {SECRET}")
        overrides = self.factory.overrides.get(call_index, {})
        values = {
            "expression": "(var x1)",
            "input_tokens": 10,
            "output_tokens": 2,
            "latency_ms": 3.5,
            "provider_request_count": 1,
            "seed_supported": False,
            "provider_model": (
                DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL
                if self.factory.provider_profile
                == DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
                else DEVELOPMENT_PILOT_MODEL
            ),
            "provider_fingerprint": (
                None
                if self.factory.provider_profile
                == DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
                else SECRET
            ),
            "finish_reason": "stop",
            "prompt_cache_hit_tokens": (
                None
                if self.factory.provider_profile
                == DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
                else 3
            ),
            "prompt_cache_miss_tokens": (
                None
                if self.factory.provider_profile
                == DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
                else 7
            ),
            "reasoning_tokens": 0,
            "candidate_format": "json_expression",
        }
        values.update(overrides)
        return GenerationResponse(**values)


class FakeLiveFactory:
    evidence = True
    evidence_reason = "offline unit-test fake"
    mode = "untrusted-test-mode"

    def __init__(
        self,
        *,
        fail_after: int | None = None,
        overrides: dict[int, dict] | None = None,
        provider_profile: str = DEVELOPMENT_PILOT_OFFICIAL_PROVIDER,
    ) -> None:
        self.calls: list[dict] = []
        self.contexts: list[GeneratorContext] = []
        self.fail_after = fail_after
        self.overrides = {} if overrides is None else overrides
        self.provider_profile = provider_profile

    def __call__(self, context: GeneratorContext) -> FakeLiveGenerator:
        self.contexts.append(context)
        return FakeLiveGenerator(self)


class CompleteFactoryStats:
    started_requests = DEVELOPMENT_PILOT_EXPECTED_CALLS
    successful_responses = DEVELOPMENT_PILOT_EXPECTED_CALLS
    expected_responses = DEVELOPMENT_PILOT_EXPECTED_CALLS
    generators_created = DEVELOPMENT_PILOT_EXPECTED_RUNS
    provider_profile = DEVELOPMENT_PILOT_OFFICIAL_PROVIDER


class CompleteVolcengineFactoryStats(CompleteFactoryStats):
    provider_profile = DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER


class DevelopmentPilotConfigTests(unittest.TestCase):
    def test_checked_in_config_is_exactly_eight_worlds_seven_arms_and_1120_calls(self) -> None:
        config = validate_development_pilot_config()

        self.assertEqual(config["worlds"], list(DEVELOPMENT_PILOT_WORLDS))
        self.assertEqual(config["episode"], DEVELOPMENT_PILOT_EPISODE)
        self.assertEqual(config["arms"], DEVELOPMENT_PILOT_ARMS)
        self.assertEqual(config["model"]["name"], DEVELOPMENT_PILOT_MODEL)
        self.assertIsNone(config["model"]["snapshot"])
        planned = (
            len(config["worlds"])
            * len(config["arms"])
            * config["episode"]["rounds"]
            * config["episode"]["candidates_per_round"]
        )
        self.assertEqual(planned, DEVELOPMENT_PILOT_EXPECTED_CALLS)

    def test_volcengine_config_differs_only_by_audited_provider_profile(self) -> None:
        official = validate_development_pilot_config(DEVELOPMENT_PILOT_CONFIG_PATH)
        volcengine = validate_development_pilot_config(
            DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH
        )

        self.assertEqual(
            volcengine["model"]["provider"], DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
        )
        normalized = copy.deepcopy(volcengine)
        normalized["model"]["provider"] = DEVELOPMENT_PILOT_OFFICIAL_PROVIDER
        self.assertEqual(normalized, official)

    def test_volcengine_preflight_binds_model_endpoint_and_rejects_unknown_profile(self) -> None:
        volc_credentials = credentials(base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT + "/")
        validated = preflight_development_pilot(
            volc_credentials,
            config=DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH,
        )
        self.assertEqual(
            validated["model"]["provider"], DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
        )

        with self.assertRaisesRegex(DevelopmentPilotError, "Volcengine endpoint"):
            preflight_development_pilot(
                credentials(base_url="https://api.deepseek.com"),
                config=DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH,
            )
        with self.assertRaisesRegex(DevelopmentPilotError, "credential model"):
            preflight_development_pilot(
                credentials(
                    "wrong-model",
                    base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
                ),
                config=DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH,
            )
        unknown = validate_development_pilot_config()
        unknown["model"]["provider"] = "unknown-provider"
        with self.assertRaisesRegex(DevelopmentPilotError, "audited provider profile"):
            preflight_development_pilot(credentials(), config=unknown)

    def test_preflight_rejects_every_contract_or_credential_model_drift(self) -> None:
        baseline = validate_development_pilot_config()
        drifts: list[dict] = []

        world = copy.deepcopy(baseline)
        world["worlds"][7]["seed"] = 9999
        drifts.append(world)

        depth = copy.deepcopy(baseline)
        depth["worlds"][0]["depth"] = 5
        drifts.append(depth)

        episode = copy.deepcopy(baseline)
        episode["episode"]["archive_size"] = 3
        drifts.append(episode)

        arms = copy.deepcopy(baseline)
        arms["arms"]["MTX"]["temperatures"] = [0.2, 0.2, 0.7, 1.2]
        drifts.append(arms)

        model = copy.deepcopy(baseline)
        model["model"]["snapshot"] = "mutable-alias"
        drifts.append(model)

        extra = copy.deepcopy(baseline)
        extra["unreviewed"] = True
        drifts.append(extra)

        for drift in drifts:
            with self.subTest(drift=drift):
                with self.assertRaises(DevelopmentPilotError):
                    preflight_development_pilot(credentials(), config=drift)

        with self.assertRaisesRegex(DevelopmentPilotError, "credential model"):
            preflight_development_pilot(credentials("other-model"))

    def test_request_coordinates_follow_every_cyclic_world_rotation(self) -> None:
        for world_index in range(len(DEVELOPMENT_PILOT_WORLDS)):
            expected_order = _arm_execution_order(DEVELOPMENT_PILOT_ARMS, world_index)
            for arm_position, arm_id in enumerate(expected_order):
                run_index = world_index * len(DEVELOPMENT_PILOT_ARMS) + arm_position
                first = _request_coordinates(
                    run_index * DEVELOPMENT_PILOT_CALLS_PER_RUN + 1
                )
                last = _request_coordinates(
                    (run_index + 1) * DEVELOPMENT_PILOT_CALLS_PER_RUN
                )
                self.assertEqual(first["world_index"], world_index)
                self.assertEqual(first["arm_id"], arm_id)
                self.assertEqual((first["round_index"], first["candidate_index"]), (0, 0))
                self.assertEqual((last["round_index"], last["candidate_index"]), (4, 3))


class DevelopmentPilotProviderAndLedgerTests(unittest.TestCase):
    def test_live_factory_disables_thinking_seed_and_provider_retries(self) -> None:
        delegate = mock.Mock()
        with mock.patch(
            "src.development_pilot.OpenAICompatibleGeneratorFactory",
            return_value=delegate,
        ) as factory_type:
            wrapped = build_live_generator_factory(
                credentials(), progress_stream=io.StringIO()
            )

        factory_type.assert_called_once_with(
            base_url="https://api.deepseek.example/v1",
            api_key=SECRET,
            model=DEVELOPMENT_PILOT_MODEL,
            seed_supported=False,
            evidence=False,
            mode=DEVELOPMENT_PILOT_MODE,
            evidence_reason=mock.ANY,
            timeout=60.0,
            extra_body={"thinking": {"type": "disabled"}},
        )
        self.assertFalse(wrapped.evidence)
        self.assertEqual(wrapped.mode, DEVELOPMENT_PILOT_MODE)
        self.assertEqual(wrapped.expected_responses, DEVELOPMENT_PILOT_EXPECTED_CALLS)

    def test_volcengine_reporter_accepts_exact_absence_and_rejects_contract_drift(self) -> None:
        clean_delegate = FakeLiveFactory(
            provider_profile=DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
        )
        clean = ProgressReportingGeneratorFactory(
            clean_delegate,
            expected_responses=1,
            stream=io.StringIO(),
            provider_profile=DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER,
        )
        generator = clean(
            GeneratorContext(
                experiment="test",
                episode={},
                model={},
                max_output_tokens=DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
            )
        )
        response = generator.generate(
            "private prompt",
            temperature=0.2,
            max_output_tokens=DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
            round_index=0,
            candidate_index=0,
        )
        self.assertEqual(
            response.provider_model, DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL
        )
        self.assertIsNone(response.prompt_cache_hit_tokens)
        self.assertIsNone(response.provider_fingerprint)

        cases = {
            "alias": {"provider_model": DEVELOPMENT_PILOT_MODEL},
            "cache_both": {
                "prompt_cache_hit_tokens": 3,
                "prompt_cache_miss_tokens": 7,
            },
            "cache_partial": {"prompt_cache_hit_tokens": 0},
            "fingerprint": {"provider_fingerprint": "unexpected"},
        }
        for name, override in cases.items():
            with self.subTest(name=name):
                delegate = FakeLiveFactory(
                    overrides={0: override},
                    provider_profile=DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER,
                )
                with self.assertRaisesRegex(
                    DevelopmentPilotError, "provider response contract failed"
                ):
                    run_development_pilot(
                        credentials(base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT),
                        config=DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH,
                        provenance_manifest=PROVENANCE,
                        generator_factory=delegate,
                        progress_stream=io.StringIO(),
                    )
                self.assertEqual(len(delegate.calls), 1)

    def test_volcengine_live_factory_keeps_request_alias_and_thinking_disabled(self) -> None:
        delegate = mock.Mock()
        with mock.patch(
            "src.development_pilot.OpenAICompatibleGeneratorFactory",
            return_value=delegate,
        ) as factory_type:
            wrapped = build_live_generator_factory(
                credentials(base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT),
                progress_stream=io.StringIO(),
                provider_profile=DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER,
            )

        self.assertEqual(factory_type.call_args.kwargs["model"], DEVELOPMENT_PILOT_MODEL)
        self.assertEqual(
            factory_type.call_args.kwargs["extra_body"],
            {"thinking": {"type": "disabled"}},
        )
        self.assertEqual(
            wrapped.provider_profile, DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
        )

    def test_ledger_records_sanitized_started_success_and_failure_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempt.jsonl"
            ledger = AttemptLedger(
                path,
                provenance=PROVENANCE,
                config_sha256="b" * 64,
            )
            wrapped = ProgressReportingGeneratorFactory(
                FakeLiveFactory(fail_after=1),
                expected_responses=2,
                stream=io.StringIO(),
                attempt_ledger=ledger,
            )
            generator = wrapped(
                GeneratorContext(
                    experiment="test",
                    episode={},
                    model={},
                    max_output_tokens=DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
                )
            )
            generator.generate(
                "raw private prompt",
                temperature=0.2,
                max_output_tokens=DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
                round_index=0,
                candidate_index=0,
            )
            with self.assertRaises(ResponsePayloadError):
                generator.generate(
                    "second raw private prompt",
                    temperature=0.2,
                    max_output_tokens=DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
                    round_index=0,
                    candidate_index=1,
                )
            ledger.close()
            text = path.read_text(encoding="utf-8")
            rows = [json.loads(line) for line in text.splitlines()]

        self.assertEqual(
            [row["event"] for row in rows],
            [
                "attempt_started",
                "logical_request_started",
                "logical_request_succeeded",
                "logical_request_started",
                "logical_request_failed_or_ambiguous",
            ],
        )
        self.assertEqual(rows[1]["world_index"], 0)
        self.assertEqual(rows[1]["arm_id"], "L")
        self.assertEqual(rows[-1]["provider_failure_category"], "response_payload_error")
        self.assertNotIn(SECRET, text)
        self.assertNotIn("raw private prompt", text)
        self.assertNotIn("malformed assistant content", text)
        succeeded = rows[2]
        self.assertNotIn("provider_fingerprint", succeeded)
        self.assertEqual(
            succeeded["provider_fingerprint_sha256"],
            __import__("hashlib").sha256(SECRET.encode()).hexdigest(),
        )

    def test_audit_critical_response_drift_aborts_on_first_paid_response(self) -> None:
        cases = {
            "model": {"provider_model": "drifted-model"},
            "finish": {"finish_reason": "length"},
            "retry": {"provider_request_count": 2},
            "seed": {"seed_supported": True},
            "format_metadata": {"candidate_format": None},
            "cache_missing": {"prompt_cache_hit_tokens": None},
            "cache_sum": {"prompt_cache_miss_tokens": 8},
            "output_cap": {"output_tokens": 257},
            "reasoning": {"reasoning_tokens": 1},
            "fingerprint": {"provider_fingerprint": None},
        }
        for name, override in cases.items():
            with self.subTest(name=name):
                delegate = FakeLiveFactory(overrides={0: override})
                with self.assertRaisesRegex(
                    DevelopmentPilotError, "provider response contract failed"
                ):
                    run_development_pilot(
                        credentials(),
                        provenance_manifest=PROVENANCE,
                        generator_factory=delegate,
                        progress_stream=io.StringIO(),
                    )
                self.assertEqual(len(delegate.calls), 1)

        fingerprint_drift = FakeLiveFactory(
            overrides={1: {"provider_fingerprint": "second-backend"}}
        )
        with self.assertRaisesRegex(
            DevelopmentPilotError, "provider response contract failed"
        ):
            run_development_pilot(
                credentials(),
                provenance_manifest=PROVENANCE,
                generator_factory=fingerprint_drift,
                progress_stream=io.StringIO(),
            )
        self.assertEqual(len(fingerprint_drift.calls), 2)

    def test_response_contract_failure_is_recorded_without_raw_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "attempt.jsonl"
            ledger = AttemptLedger(
                path,
                provenance=PROVENANCE,
                config_sha256="b" * 64,
            )
            delegate = FakeLiveFactory(
                overrides={0: {"provider_model": f"drift-{SECRET}"}}
            )
            wrapped = ProgressReportingGeneratorFactory(
                delegate,
                stream=io.StringIO(),
                attempt_ledger=ledger,
            )
            generator = wrapped(
                GeneratorContext(
                    experiment="test",
                    episode={},
                    model={},
                    max_output_tokens=DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
                )
            )
            with self.assertRaises(DevelopmentPilotError):
                generator.generate(
                    "private prompt",
                    temperature=0.2,
                    max_output_tokens=DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
                    round_index=0,
                    candidate_index=0,
                )
            ledger.close()
            text = path.read_text(encoding="utf-8")
            rows = [json.loads(line) for line in text.splitlines()]

        self.assertEqual(rows[-1]["event"], "response_contract_failed")
        self.assertEqual(rows[-1]["failure_categories"], ["response_model_drift"])
        self.assertNotIn(SECRET, text)
        self.assertNotIn("private prompt", text)


class DevelopmentPilotRunTests(unittest.TestCase):
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.delegate = FakeLiveFactory()
        cls.progress = io.StringIO()
        cls.private_evaluations = 0
        original_evaluate = experiment_module.evaluate_episode_test

        def delayed_evaluate(*args, **kwargs):
            if len(cls.delegate.calls) != DEVELOPMENT_PILOT_EXPECTED_CALLS:
                raise AssertionError("private test was evaluated before all generation calls")
            cls.private_evaluations += 1
            return original_evaluate(*args, **kwargs)

        with mock.patch(
            "src.experiment.evaluate_episode_test",
            side_effect=delayed_evaluate,
        ):
            cls.result = run_development_pilot(
                credentials(),
                provenance_manifest=PROVENANCE,
                generator_factory=cls.delegate,
                progress_stream=cls.progress,
            )

    def test_complete_run_uses_1120_calls_and_globally_delays_private_test(self) -> None:
        self.assertEqual(len(self.delegate.calls), DEVELOPMENT_PILOT_EXPECTED_CALLS)
        self.assertEqual(len(self.delegate.contexts), DEVELOPMENT_PILOT_EXPECTED_RUNS)
        self.assertEqual(self.private_evaluations, DEVELOPMENT_PILOT_EXPECTED_RUNS)
        self.assertEqual(
            self.result["budget"]["generation_calls_completed"],
            DEVELOPMENT_PILOT_EXPECTED_CALLS,
        )
        self.assertEqual(
            self.result["budget"]["provider_requests"],
            DEVELOPMENT_PILOT_EXPECTED_CALLS,
        )
        self.assertEqual(self.result["budget"]["retry_count"], 0)
        self.assertEqual(
            self.result["budget"]["max_output_tokens_planned"],
            DEVELOPMENT_PILOT_EXPECTED_CALLS
            * DEVELOPMENT_PILOT_EPISODE["max_output_tokens"],
        )
        self.assertEqual(self.result["budget"]["max_output_tokens_planned"], 286_720)
        self.assertFalse(self.result["evidence"])
        self.assertEqual(self.result["evidence_scope"], "non-evidence")
        self.assertEqual(self.result["mode"], DEVELOPMENT_PILOT_MODE)
        self.assertEqual(self.result["provenance"], PROVENANCE)

    def test_result_drops_candidate_content_and_hashes_fingerprint(self) -> None:
        encoded = json.dumps(self.result, sort_keys=True)
        self.assertNotIn(SECRET, encoded)
        for run in self.result["runs"]:
            for candidate in run["candidates"]:
                self.assertNotIn("candidate_expression", candidate)
                self.assertTrue(candidate["provider_fingerprint"].startswith("sha256:"))
        self.assertEqual(
            self.result["model"]["observed_system_fingerprints"],
            ["sha256:" + __import__("hashlib").sha256(SECRET.encode()).hexdigest()],
        )

    def test_postflight_accepts_success_and_rejects_fingerprint_cache_or_model_drift(self) -> None:
        self.assertTrue(
            _validate_completed_summary(self.result, CompleteFactoryStats()).startswith(
                "sha256:"
            )
        )
        mutations = {
            "fingerprint": ("provider_fingerprint", "sha256:" + "f" * 64),
            "cache": ("prompt_cache_miss_tokens", 8),
            "model": ("provider_model", "drifted-model"),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                drift = copy.deepcopy(self.result)
                drift["runs"][0]["candidates"][0][field] = value
                with self.assertRaises(DevelopmentPilotError):
                    _validate_completed_summary(drift, CompleteFactoryStats())

        budget_drift = copy.deepcopy(self.result)
        budget_drift["budget"]["max_output_tokens_planned"] -= 1
        with self.assertRaises(DevelopmentPilotError):
            _validate_completed_summary(budget_drift, CompleteFactoryStats())


class DevelopmentPilotVolcengineRunTests(unittest.TestCase):
    result: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.delegate = FakeLiveFactory(
            overrides={0: {"expression": "not valid DSL"}},
            provider_profile=DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
        )
        cls.result = run_development_pilot(
            credentials(base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT + "/"),
            config=DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH,
            provenance_manifest=PROVENANCE,
            generator_factory=cls.delegate,
            progress_stream=io.StringIO(),
        )

    def test_full_volcengine_summary_preserves_missing_capabilities_and_usage(self) -> None:
        self.assertEqual(len(self.delegate.calls), DEVELOPMENT_PILOT_EXPECTED_CALLS)
        self.assertEqual(
            self.result["model"]["observed_response_models"],
            [DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL],
        )
        self.assertEqual(self.result["model"]["observed_system_fingerprints"], [])
        self.assertIsNone(self.result["budget"]["prompt_cache_hit_tokens"])
        self.assertIsNone(self.result["budget"]["prompt_cache_miss_tokens"])
        self.assertEqual(self.result["budget"]["actual_input_tokens"], 11_200)
        self.assertEqual(self.result["budget"]["actual_output_tokens"], 2_240)
        self.assertEqual(self.result["budget"]["provider_requests"], 1_120)
        self.assertEqual(self.result["budget"]["max_output_tokens_planned"], 286_720)
        self.assertFalse(self.result["runs"][0]["candidates"][0]["syntax_valid"])
        self.assertFalse(self.result["runs"][0]["candidates"][0]["runtime_valid"])
        for run in self.result["runs"]:
            self.assertIsNone(run["budget"]["prompt_cache_hit_tokens"])
            self.assertIsNone(run["budget"]["prompt_cache_miss_tokens"])
            for candidate in run["candidates"]:
                self.assertIsNone(candidate["prompt_cache_hit_tokens"])
                self.assertIsNone(candidate["prompt_cache_miss_tokens"])
                self.assertIsNone(candidate["provider_fingerprint"])
                self.assertNotIn("candidate_expression", candidate)
        self.assertIsNone(
            _validate_completed_summary(
                self.result,
                CompleteVolcengineFactoryStats(),
            )
        )

    def test_volcengine_artifact_has_only_hashed_endpoint_and_safe_contract(self) -> None:
        contract = self.result["provider_contract"]
        self.assertEqual(contract["profile"], DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER)
        self.assertEqual(contract["request_model"], DEVELOPMENT_PILOT_MODEL)
        self.assertEqual(
            contract["expected_response_model"],
            DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL,
        )
        self.assertTrue(contract["contract_satisfied"])
        self.assertTrue(contract["endpoint_contract"]["contract_satisfied"])
        self.assertEqual(
            contract["endpoint_contract"]["normalized_url_sha256"],
            __import__("hashlib").sha256(
                DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT.encode()
            ).hexdigest(),
        )
        encoded = json.dumps(self.result, sort_keys=True)
        self.assertNotIn(DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT, encoded)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn("candidate_expression", encoded)

    def test_volcengine_postflight_rejects_alias_telemetry_or_summary_drift(self) -> None:
        mutations = {
            "candidate_alias": ("candidate", "provider_model", DEVELOPMENT_PILOT_MODEL),
            "candidate_cache": ("candidate", "prompt_cache_hit_tokens", 0),
            "candidate_fingerprint": ("candidate", "provider_fingerprint", "fp"),
            "run_cache": ("run_budget", "prompt_cache_hit_tokens", 0),
            "top_cache": ("top_budget", "prompt_cache_miss_tokens", 0),
            "model_ledger": ("model", "observed_response_models", [DEVELOPMENT_PILOT_MODEL]),
        }
        for name, (location, field, value) in mutations.items():
            with self.subTest(name=name):
                drift = copy.deepcopy(self.result)
                if location == "candidate":
                    drift["runs"][0]["candidates"][0][field] = value
                elif location == "run_budget":
                    drift["runs"][0]["budget"][field] = value
                elif location == "top_budget":
                    drift["budget"][field] = value
                else:
                    drift["model"][field] = value
                with self.assertRaises(DevelopmentPilotError):
                    _validate_completed_summary(
                        drift,
                        CompleteVolcengineFactoryStats(),
                    )


class DevelopmentPilotArtifactAndCliTests(unittest.TestCase):
    def test_artifact_is_exclusive_secret_checked_and_rejects_raw_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pilot.json"
            _write_new_json(output, {"safe": True}, forbidden_values=(SECRET,))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"safe": True})
            with self.assertRaises(FileExistsError):
                _write_new_json(output, {"safe": False}, forbidden_values=(SECRET,))

            secret_output = Path(directory) / "secret.json"
            with self.assertRaises(DevelopmentPilotError):
                _write_new_json(
                    secret_output,
                    {"accidental": SECRET},
                    forbidden_values=(SECRET,),
                )
            self.assertFalse(secret_output.exists())

            raw_output = Path(directory) / "raw.json"
            with self.assertRaises(DevelopmentPilotError):
                _write_new_json(raw_output, {"candidate_expression": "(var x1)"})
            self.assertFalse(raw_output.exists())

    def test_cli_requires_execute_rejects_resume_and_checks_paths_first(self) -> None:
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

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.write_text("untouched", encoding="utf-8")
            with (
                mock.patch("src.development_pilot.load_provider_credentials") as load,
                contextlib.redirect_stderr(io.StringIO()),
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
            self.assertEqual(output.read_text(encoding="utf-8"), "untouched")

    def test_cli_failure_keeps_ledger_but_never_result_or_exception_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            ledger = Path(directory) / "attempt.jsonl"
            stderr = io.StringIO()
            with (
                mock.patch(
                    "src.development_pilot.load_provider_credentials",
                    return_value=credentials(),
                ),
                mock.patch(
                    "src.development_pilot.preflight_development_pilot",
                    return_value=validate_development_pilot_config(),
                ),
                mock.patch(
                    "src.development_pilot.source_manifest",
                    return_value=PROVENANCE,
                ),
                mock.patch(
                    "src.development_pilot.run_development_pilot",
                    side_effect=RuntimeError(f"unsafe exception text {SECRET}"),
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

            self.assertEqual(status, 1)
            self.assertFalse(output.exists())
            self.assertTrue(ledger.exists())
            durable_text = ledger.read_text(encoding="utf-8")
            self.assertNotIn(SECRET, durable_text)
            self.assertNotIn(SECRET, stderr.getvalue())
            self.assertIn("attempt_aborted", durable_text)

    def test_cli_rejects_wrong_volcengine_endpoint_before_ledger_or_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            ledger = Path(directory) / "attempt.jsonl"
            with (
                mock.patch(
                    "src.development_pilot.load_provider_credentials",
                    return_value=credentials(base_url="https://api.deepseek.com"),
                ),
                mock.patch("src.development_pilot.run_development_pilot") as run,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                status = main(
                    [
                        "--env-file",
                        "credentials.env",
                        "--config",
                        str(DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH),
                        "--output",
                        str(output),
                        "--attempt-ledger",
                        str(ledger),
                        "--execute",
                    ]
                )

            self.assertEqual(status, 1)
            run.assert_not_called()
            self.assertFalse(output.exists())
            self.assertFalse(ledger.exists())


if __name__ == "__main__":
    unittest.main()
