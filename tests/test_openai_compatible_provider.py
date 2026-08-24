from __future__ import annotations

import copy
import hashlib
import json
import pickle
import socket
import unittest
import urllib.error
from types import MappingProxyType

from src.experiment import GeneratorContext
from src.providers.openai_compatible import (
    HTTPResponse,
    HTTPStatusError,
    OpenAICompatibleGenerator,
    OpenAICompatibleGeneratorFactory,
    PreparedRequest,
    ResponsePayloadError,
    TransportError,
    UrllibHTTPTransport,
    UsagePayloadError,
    normalize_chat_completions_url,
)
from src.runner import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    FixedTemperaturePolicy,
    GenerationResponse,
    run_episode,
)
from src.world_generator import generate_world


SECRET = "unit-test-secret-must-not-escape"
EXPRESSION = "(add (var x1) (const 1))"


def successful_response(
    *,
    expression: str = EXPRESSION,
    prompt_tokens: object = 19,
    completion_tokens: object = 7,
) -> HTTPResponse:
    payload = {
        "id": "chatcmpl-unit-test",
        "model": "provider-model-snapshot",
        "system_fingerprint": "fp_unit_test_backend",
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
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": 26,
            "prompt_cache_hit_tokens": 11,
            "prompt_cache_miss_tokens": 8,
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }
    return HTTPResponse(200, json.dumps(payload).encode("utf-8"))


def context() -> GeneratorContext:
    return GeneratorContext(
        experiment="provider-unit-test",
        episode=MappingProxyType({"rounds": 1}),
        model=MappingProxyType(
            {
                "provider": "openai-compatible",
                "name": "provider-model",
                "snapshot": "snapshot",
                "structured_output": True,
            }
        ),
        max_output_tokens=31,
    )


class RecordingTransport:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, **kwargs: object) -> HTTPResponse:
        self.calls.append(
            {
                **kwargs,
                "headers": dict(kwargs["headers"]),
            }
        )
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response  # type: ignore[return-value]


class SequenceClock:
    def __init__(self, *values: float) -> None:
        self.values = iter(values)

    def __call__(self) -> float:
        return next(self.values)


class EndpointTests(unittest.TestCase):
    def test_normalizes_roots_versions_and_complete_endpoints(self) -> None:
        cases = {
            "https://provider.example": "https://provider.example/chat/completions",
            "https://provider.example/": "https://provider.example/chat/completions",
            "https://provider.example/v1": "https://provider.example/v1/chat/completions",
            "https://provider.example/v1/": "https://provider.example/v1/chat/completions",
            "https://provider.example/v1/chat/completions/": (
                "https://provider.example/v1/chat/completions"
            ),
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_chat_completions_url(value), expected)

    def test_rejects_unsafe_or_non_absolute_urls(self) -> None:
        for value in (
            "provider.example/v1",
            "ftp://provider.example/v1",
            "https://user:password@provider.example/v1",
            "https://provider.example/v1?key=value",
            "https://provider.example/v1#fragment",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_chat_completions_url(value)


class RequestAndFactoryTests(unittest.TestCase):
    def test_prepared_request_reuses_the_exact_body_bytes_without_network_on_prepare(
        self,
    ) -> None:
        transport = RecordingTransport(successful_response())
        generator = OpenAICompatibleGenerator(
            base_url="https://provider.example/v1",
            api_key=SECRET,
            model="provider-model",
            seed_supported=True,
            transport=transport,
            clock=SequenceClock(1.0, 1.1, 2.0, 2.2),
        )

        prepared = generator.prepare_request(
            "infer this prepared rule",
            temperature=0.7,
            max_output_tokens=31,
            seed=1729,
            round_index=2,
            candidate_index=3,
            state={"best_probe_score": 0.5},
        )

        self.assertIsInstance(prepared, PreparedRequest)
        self.assertEqual(transport.calls, [])
        first = generator.send_prepared(prepared)
        second = generator.send_prepared(prepared)

        self.assertEqual(len(transport.calls), 2)
        first_body = transport.calls[0]["body"]
        second_body = transport.calls[1]["body"]
        self.assertIs(first_body, second_body)
        self.assertEqual(first_body, second_body)
        self.assertEqual(
            prepared.body_sha256,
            hashlib.sha256(first_body).hexdigest(),  # type: ignore[arg-type]
        )
        self.assertEqual(prepared.size_bytes, len(first_body))  # type: ignore[arg-type]
        self.assertEqual(first.expression, EXPRESSION)
        self.assertEqual(second.expression, EXPRESSION)
        self.assertEqual(first.provider_request_count, 1)
        self.assertEqual(second.provider_request_count, 1)

    def test_prepared_request_is_opaque_immutable_and_not_serializable(self) -> None:
        raw_prompt = "raw prompt must never appear in prepared representations"
        generator = OpenAICompatibleGenerator(
            base_url="https://provider.example/private-route",
            api_key=SECRET,
            model="provider-model",
            transport=RecordingTransport(successful_response()),
        )
        prepared = generator.prepare_request(raw_prompt, temperature=0.2)

        views = (
            repr(prepared),
            str(prepared),
            json.dumps({"prepared": prepared}, default=repr),
        )
        for view in views:
            self.assertNotIn(raw_prompt, view)
            self.assertNotIn(SECRET, view)
            self.assertNotIn("provider.example", view)
        self.assertFalse(hasattr(prepared, "__dict__"))
        with self.assertRaisesRegex(TypeError, "cannot be copied or serialized"):
            pickle.dumps(prepared)
        with self.assertRaisesRegex(TypeError, "cannot be copied or serialized"):
            copy.copy(prepared)
        with self.assertRaisesRegex(TypeError, "cannot be copied or serialized"):
            copy.deepcopy(prepared)
        with self.assertRaisesRegex(AttributeError, "immutable"):
            prepared.body_sha256 = "0" * 64  # type: ignore[misc]
        with self.assertRaisesRegex(TypeError, "created by"):
            PreparedRequest()

    def test_prepared_request_is_bound_to_the_generator_that_created_it(self) -> None:
        first_transport = RecordingTransport(successful_response())
        second_transport = RecordingTransport(successful_response())
        first = OpenAICompatibleGenerator(
            base_url="https://provider.example",
            api_key=SECRET,
            model="provider-model",
            transport=first_transport,
        )
        second = OpenAICompatibleGenerator(
            base_url="https://provider.example",
            api_key=SECRET,
            model="provider-model",
            transport=second_transport,
        )
        prepared = first.prepare_request("prompt", temperature=0.2)

        with self.assertRaisesRegex(ValueError, "different generator"):
            second.send_prepared(prepared)

        self.assertEqual(first_transport.calls, [])
        self.assertEqual(second_transport.calls, [])

    def test_generator_route_fields_are_read_only_after_construction(self) -> None:
        generator = OpenAICompatibleGenerator(
            base_url="https://provider.example/v1",
            api_key=SECRET,
            model="provider-model",
            seed_supported=False,
            timeout=60.0,
        )
        for field, value in (
            ("endpoint", "https://other.example/chat/completions"),
            ("timeout", 1.0),
            ("seed_supported", True),
        ):
            with self.subTest(field=field):
                with self.assertRaises(AttributeError):
                    setattr(generator, field, value)

    def test_generator_default_uses_the_amended_256_token_cap(self) -> None:
        transport = RecordingTransport(successful_response())
        generator = OpenAICompatibleGenerator(
            base_url="https://provider.example/v1/",
            api_key=SECRET,
            model="provider-model",
            transport=transport,
        )

        generator.generate("infer this rule", temperature=0.7)

        payload = json.loads(transport.calls[0]["body"])
        self.assertEqual(DEFAULT_MAX_OUTPUT_TOKENS, 256)
        self.assertEqual(payload["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS)

    def test_factory_posts_exact_experimental_fields_and_static_extensions(self) -> None:
        transport = RecordingTransport(successful_response())
        factory = OpenAICompatibleGeneratorFactory(
            base_url="https://provider.example/v1/",
            api_key=SECRET,
            model="provider-model",
            seed_supported=True,
            evidence=True,
            mode="development-provider",
            extra_body={"thinking": {"type": "disabled"}},
            request_overrides={"stream": False},
            transport=transport,
            clock=SequenceClock(10.0, 10.125),
        )

        generator = factory(context())
        result = generator.generate(
            "infer this rule",
            temperature=0.7,
            max_output_tokens=31,
            seed=1729,
            round_index=3,
            candidate_index=2,
            state={"best_probe_score": 0.5},
        )

        self.assertEqual(len(transport.calls), 1)
        call = transport.calls[0]
        self.assertEqual(call["url"], "https://provider.example/v1/chat/completions")
        self.assertEqual(call["timeout"], 60.0)
        self.assertEqual(
            json.loads(call["body"]),
            {
                "model": "provider-model",
                "messages": [{"role": "user", "content": "infer this rule"}],
                "temperature": 0.7,
                "max_tokens": 31,
                "response_format": {"type": "json_object"},
                "seed": 1729,
                "thinking": {"type": "disabled"},
                "stream": False,
            },
        )
        self.assertEqual(
            call["headers"],
            {
                "Authorization": f"Bearer {SECRET}",
                "Content-Type": "application/json",
            },
        )
        self.assertIsInstance(result, GenerationResponse)
        self.assertEqual(result.expression, EXPRESSION)
        self.assertEqual(result.input_tokens, 19)
        self.assertEqual(result.output_tokens, 7)
        self.assertEqual(result.latency_ms, 125.0)
        self.assertEqual(result.provider_request_count, 1)
        self.assertIs(result.seed_supported, True)
        self.assertEqual(result.provider_model, "provider-model-snapshot")
        self.assertEqual(result.provider_fingerprint, "fp_unit_test_backend")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.prompt_cache_hit_tokens, 11)
        self.assertEqual(result.prompt_cache_miss_tokens, 8)
        self.assertEqual(result.reasoning_tokens, 0)
        self.assertEqual(result.candidate_format, "json_expression")
        self.assertIsNone(result.raw)
        self.assertIs(factory.evidence, True)
        self.assertEqual(factory.mode, "development-provider")

    def test_seed_is_omitted_when_support_is_not_declared(self) -> None:
        transport = RecordingTransport(successful_response())
        generator = OpenAICompatibleGenerator(
            base_url="https://provider.example",
            api_key=SECRET,
            model="provider-model",
            seed_supported=False,
            transport=transport,
        )

        result = generator.generate("prompt", temperature=0.2, seed=1729)

        self.assertNotIn("seed", json.loads(transport.calls[0]["body"]))
        self.assertIs(result.seed_supported, False)

    def test_factory_and_generator_representations_redact_the_key(self) -> None:
        factory = OpenAICompatibleGeneratorFactory(
            base_url="https://provider.example",
            api_key=SECRET,
            model="provider-model",
        )
        generator = factory(context())

        serialized_views = json.dumps(
            {"factory": factory, "generator": generator},
            default=repr,
            sort_keys=True,
        )
        self.assertNotIn(SECRET, serialized_views)
        self.assertNotIn(SECRET, repr(factory))
        self.assertNotIn(SECRET, repr(generator))

        contract = generator.sanitized_request_contract()
        encoded_contract = json.dumps(contract, sort_keys=True)
        self.assertNotIn(SECRET, encoded_contract)
        self.assertNotIn("https://provider.example", encoded_contract)
        self.assertEqual(contract["request_model"], "provider-model")
        self.assertEqual(contract["timeout_seconds"], 60.0)
        self.assertEqual(len(contract["endpoint_sha256"]), 64)
        self.assertEqual(
            contract["transport_profile"], "stdlib-urllib-one-shot-v1"
        )

        live_contract = OpenAICompatibleGenerator(
            base_url="https://provider.example",
            api_key=SECRET,
            model="provider-model",
        ).sanitized_request_contract()
        self.assertEqual(
            live_contract["transport_profile"], "stdlib-urllib-one-shot-v1"
        )

    def test_static_fields_are_detached_and_protected_fields_are_rejected(self) -> None:
        thinking = {"type": "disabled"}
        transport = RecordingTransport(successful_response())
        generator = OpenAICompatibleGenerator(
            base_url="https://provider.example",
            api_key=SECRET,
            model="provider-model",
            extra_body={"thinking": thinking},
            transport=transport,
        )
        thinking["type"] = "enabled"
        generator.generate("prompt", temperature=0.2)
        sent = json.loads(transport.calls[0]["body"])
        self.assertEqual(sent["thinking"], {"type": "disabled"})

        for protected in (
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "response_format",
            "seed",
        ):
            with self.subTest(protected=protected):
                with self.assertRaisesRegex(ValueError, "protected request fields"):
                    OpenAICompatibleGenerator(
                        base_url="https://provider.example",
                        api_key=SECRET,
                        model="provider-model",
                        request_overrides={protected: "replacement"},
                    )

    def test_thinking_cannot_be_enabled_because_temperature_is_the_treatment(self) -> None:
        with self.assertRaisesRegex(ValueError, "temperature remains the treatment"):
            OpenAICompatibleGeneratorFactory(
                base_url="https://provider.example",
                api_key=SECRET,
                model="provider-model",
                extra_body={"thinking": {"type": "enabled"}},
            )

    def test_factory_rejects_model_config_drift_before_any_request(self) -> None:
        transport = RecordingTransport(successful_response())
        factory = OpenAICompatibleGeneratorFactory(
            base_url="https://provider.example",
            api_key=SECRET,
            model="different-model",
            transport=transport,
        )

        with self.assertRaisesRegex(ValueError, "exactly match"):
            factory(context())
        self.assertEqual(transport.calls, [])


class FailureTests(unittest.TestCase):
    def generator(self, response: object) -> tuple[OpenAICompatibleGenerator, RecordingTransport]:
        transport = RecordingTransport(response)
        generator = OpenAICompatibleGenerator(
            base_url="https://provider.example",
            api_key=SECRET,
            model="provider-model",
            transport=transport,
        )
        return generator, transport

    def test_http_failure_is_clear_and_never_retried(self) -> None:
        generator, transport = self.generator(HTTPResponse(429, b'{"error":"limited"}'))

        with self.assertRaisesRegex(HTTPStatusError, "HTTP status 429") as raised:
            generator.generate("prompt", temperature=0.7)

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(SECRET, str(raised.exception))

    def test_retryable_physical_attempt_classification_is_closed(self) -> None:
        retryable_transport = {
            "timeout",
            "dns",
            "tls",
            "connection_refused",
            "connection_reset",
            "network_io",
        }
        for category in TransportError._CATEGORIES:
            with self.subTest(category=category):
                error = TransportError(
                    category=category,
                    delivery_ambiguous=category not in {
                        "local_request_configuration",
                        "local_transport_contract",
                    },
                )
                self.assertIs(
                    error.retryable_physical_attempt,
                    category in retryable_transport,
                )

        for status in (400, 408, 429, 499, 500, 503, 599):
            with self.subTest(status=status):
                self.assertIs(
                    HTTPStatusError(status).retryable_physical_attempt,
                    status == 429 or 500 <= status <= 599,
                )

    def test_send_prepared_keeps_2xx_content_and_outer_contract_semantics(self) -> None:
        malformed_content = json.loads(successful_response().body)
        malformed_content["choices"][0]["message"]["content"] = "not json"
        content_generator, content_transport = self.generator(
            HTTPResponse(200, json.dumps(malformed_content).encode("utf-8"))
        )
        prepared = content_generator.prepare_request("prompt", temperature=0.7)

        response = content_generator.send_prepared(prepared)

        self.assertEqual(response.candidate_format, "invalid_json")
        self.assertEqual(len(content_transport.calls), 1)

        envelope_generator, envelope_transport = self.generator(HTTPResponse(200, b"{}"))
        prepared = envelope_generator.prepare_request("prompt", temperature=0.7)
        with self.assertRaisesRegex(ResponsePayloadError, "choices"):
            envelope_generator.send_prepared(prepared)
        self.assertEqual(len(envelope_transport.calls), 1)

    def test_transport_exception_is_normalized_without_secret_and_never_retried(self) -> None:
        generator, transport = self.generator(RuntimeError(f"failed with {SECRET}"))

        with self.assertRaisesRegex(TransportError, "HTTP transport failed") as raised:
            generator.generate("prompt", temperature=0.7)

        self.assertEqual(len(transport.calls), 1)
        self.assertNotIn(SECRET, str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(raised.exception.category, "injected_transport_exception")
        self.assertIs(raised.exception.delivery_ambiguous, True)
        self.assertEqual(raised.exception.recovery_scope, "restart_whole_shard")

    def test_transport_classification_is_closed_and_preserved(self) -> None:
        original = TransportError(
            category="timeout",
            delivery_ambiguous=True,
        )
        generator, transport = self.generator(original)

        with self.assertRaises(TransportError) as raised:
            generator.generate("prompt", temperature=0.7)

        self.assertIs(raised.exception, original)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(raised.exception.category, "timeout")
        self.assertEqual(raised.exception.recovery_scope, "restart_whole_shard")
        with self.assertRaisesRegex(ValueError, "closed set"):
            TransportError(category="unreviewed_failure")

    def test_urllib_timeout_is_delivery_ambiguous_without_leaking_details(self) -> None:
        class TimeoutOpener:
            def open(self, request: object, timeout: float) -> object:
                del request, timeout
                raise urllib.error.URLError(socket.timeout(f"timed out {SECRET}"))

        transport = UrllibHTTPTransport()
        transport._opener = TimeoutOpener()  # type: ignore[assignment]

        with self.assertRaises(TransportError) as raised:
            transport.post(
                url="https://provider.example/chat/completions",
                headers={},
                body=b"{}",
                timeout=60.0,
            )

        self.assertEqual(raised.exception.category, "timeout")
        self.assertIs(raised.exception.delivery_ambiguous, True)
        self.assertNotIn(SECRET, str(raised.exception))

    def test_invalid_http_and_outer_json_payloads_are_rejected(self) -> None:
        multiple_choices = json.loads(successful_response().body)
        multiple_choices["choices"].append(dict(multiple_choices["choices"][0]))
        cases = (
            (HTTPResponse(True, b"{}"), TransportError, "status code"),
            (HTTPResponse(200, "{}"), TransportError, "non-bytes"),
            (HTTPResponse(200, b"\xff"), ResponsePayloadError, "UTF-8"),
            (HTTPResponse(200, b"not json"), ResponsePayloadError, "valid JSON"),
            (HTTPResponse(200, b"[]"), ResponsePayloadError, "JSON object"),
            (HTTPResponse(200, b"{}"), ResponsePayloadError, "choices"),
            (
                HTTPResponse(200, json.dumps(multiple_choices).encode("utf-8")),
                ResponsePayloadError,
                "exactly one",
            ),
        )
        for response, error, message in cases:
            with self.subTest(response=response):
                generator, transport = self.generator(response)
                with self.assertRaisesRegex(error, message):
                    generator.generate("prompt", temperature=0.7)
                self.assertEqual(len(transport.calls), 1)

    def test_invalid_transport_response_is_campaign_fatal_not_recoverable(self) -> None:
        for response in (HTTPResponse(True, b"{}"), HTTPResponse(200, "{}")):
            with self.subTest(response=response):
                generator, transport = self.generator(response)
                with self.assertRaises(TransportError) as raised:
                    generator.generate("prompt", temperature=0.7)
                self.assertEqual(len(transport.calls), 1)
                self.assertEqual(raised.exception.category, "local_transport_contract")
                self.assertIs(raised.exception.delivery_ambiguous, False)
                self.assertEqual(raised.exception.recovery_scope, "campaign_fatal")

    def test_malformed_system_fingerprint_is_rejected(self) -> None:
        for value in ("", "   ", 17, ["fp"]):
            with self.subTest(value=value):
                payload = json.loads(successful_response().body)
                payload["system_fingerprint"] = value
                generator, transport = self.generator(
                    HTTPResponse(200, json.dumps(payload).encode("utf-8"))
                )
                with self.assertRaisesRegex(
                    ResponsePayloadError, "system_fingerprint"
                ):
                    generator.generate("prompt", temperature=0.7)
                self.assertEqual(len(transport.calls), 1)

    def test_malformed_assistant_content_becomes_candidate_failure(self) -> None:
        cases = (
            (None, "null_content"),
            ("", "empty_content"),
            (f"not json {SECRET}", "invalid_json"),
            (json.dumps([SECRET]), "json_non_object"),
            (json.dumps({"unrelated": SECRET}), "missing_expression"),
            (
                json.dumps({"expression": EXPRESSION, "rationale": SECRET}),
                "extra_fields",
            ),
            (
                '{"expression":"first","expression":"second"}',
                "extra_fields",
            ),
            (
                json.dumps({"expression": [SECRET]}),
                "non_string_expression",
            ),
            (json.dumps({"expression": "  "}), "empty_expression"),
            ([SECRET], "non_string_content"),
        )
        expressions = []
        for content, expected_format in cases:
            with self.subTest(candidate_format=expected_format):
                payload = {
                    "model": "provider-model-snapshot",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": content},
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                generator, _ = self.generator(
                    HTTPResponse(200, json.dumps(payload).encode("utf-8"))
                )
                response = generator.generate("prompt", temperature=0.7)
                expressions.append(response.expression)
                self.assertIsInstance(response.expression, str)
                self.assertEqual(response.candidate_format, expected_format)
                self.assertEqual(response.input_tokens, 1)
                self.assertEqual(response.output_tokens, 1)
                self.assertIsNone(response.raw)
                self.assertNotIn(SECRET, repr(response))

        self.assertEqual(len(set(expressions)), 1)
        self.assertNotIn("var", expressions[0])
        self.assertNotIn(SECRET, expressions[0])

        payload = {
            "model": "provider-model-snapshot",
            "system_fingerprint": "fp_unit_test_backend",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": ["var", "x1"]},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        generator, _ = self.generator(
            HTTPResponse(200, json.dumps(payload).encode("utf-8"))
        )
        result = run_episode(
            generate_world(seed=1000, depth=3),
            generator,
            policy=FixedTemperaturePolicy(0.2),
            rounds=1,
            candidates_per_round=1,
            evaluate_test=False,
        )
        record = result.rounds[0][0]
        self.assertFalse(record.syntax_valid)
        self.assertEqual(record.failure_codes, ("parse_or_grammar",))
        self.assertEqual((record.input_tokens, record.output_tokens), (1, 1))
        self.assertEqual(record.candidate_format, "non_string_content")
        self.assertNotIn(SECRET, repr(record))

    def test_missing_or_non_integer_usage_is_rejected(self) -> None:
        missing_usage = {
            "model": "provider-model-snapshot",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"expression": EXPRESSION})},
                }
            ]
        }
        cases = (
            (missing_usage, "response.usage"),
            (
                json.loads(successful_response(prompt_tokens=True).body),
                "usage.prompt_tokens",
            ),
            (
                json.loads(successful_response(completion_tokens=-1).body),
                "usage.completion_tokens",
            ),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                generator, _ = self.generator(
                    HTTPResponse(200, json.dumps(payload).encode("utf-8"))
                )
                with self.assertRaisesRegex(UsagePayloadError, message):
                    generator.generate("prompt", temperature=0.7)


if __name__ == "__main__":
    unittest.main()
