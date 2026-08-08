"""Guarded live runner for the single-world development gate.

The command deliberately has no checkpoint or resume mode.  A provider error
or interruption yields no result artifact, and a new invocation starts all 140
calls again.  Operators must account for requests from an interrupted attempt
separately; this command never merges partial attempts into an apparently
complete experiment.

This is development-only.  It is not a confirmatory runner, and hidden-test
outcomes must not be used to tune or freeze the protocol.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from typing import Any, TextIO

from .credentials import ProviderCredentials, load_provider_credentials
from .experiment import (
    ARM_EXECUTION_BASE_ORDER,
    GeneratorContext,
    load_config,
    run_experiment,
    validate_config,
)
from .providers import (
    HTTPStatusError,
    OpenAICompatibleError,
    OpenAICompatibleGeneratorFactory,
    ResponsePayloadError,
    TransportError,
    UsagePayloadError,
)
from .provenance import PROJECT_ROOT, source_manifest
from .runner import CANDIDATE_FORMATS, DEFAULT_MAX_OUTPUT_TOKENS, GenerationResponse


DEVELOPMENT_GATE_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "development-gate.json"
)
DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "development-gate-volcengine.json"
)
DEVELOPMENT_GATE_MODEL = "deepseek-v4-flash"
DEVELOPMENT_GATE_OFFICIAL_PROVIDER = "deepseek-openai-compatible"
DEVELOPMENT_GATE_VOLCENGINE_PROVIDER = (
    "volcengine-agent-plan-openai-compatible"
)
DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL = "deepseek-v4-flash-ga-260731"
DEVELOPMENT_GATE_VOLCENGINE_ENDPOINT = (
    "https://ark.cn-beijing.volces.com/api/plan/v3"
)
DEVELOPMENT_GATE_WORLD = {"seed": 2000, "depth": 3}
DEVELOPMENT_GATE_EPISODE = {
    "rounds": 5,
    "candidates_per_round": 4,
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    "archive_size": 4,
    "max_counterexamples_per_round": 2,
}
DEVELOPMENT_GATE_ARMS = {
    "L": {"kind": "fixed", "temperature": 0.2},
    "M": {"kind": "fixed", "temperature": 0.7},
    "H": {"kind": "fixed", "temperature": 1.2},
    "A": {"kind": "sequence", "temperatures": [1.2, 0.95, 0.7, 0.45, 0.2]},
    "C": {"kind": "sequence", "temperatures": [1.2, 0.2, 1.2, 0.2, 0.2]},
    "MTX": {"kind": "multi", "temperatures": [0.2, 0.7, 0.7, 1.2]},
    "E": {
        "kind": "adaptive",
        "initial_temperature": 1.0,
        "minimum_temperature": 0.2,
        "maximum_temperature": 1.2,
        "improvement_step": -0.2,
        "stagnation_step": 0.3,
    },
}
DEVELOPMENT_GATE_MODEL_CONFIG = {
    "provider": DEVELOPMENT_GATE_OFFICIAL_PROVIDER,
    "name": DEVELOPMENT_GATE_MODEL,
    # The provider echoed only a movable alias. This operational gate must not
    # promote it to an immutable scientific snapshot.
    "snapshot": None,
    "structured_output": True,
}
DEVELOPMENT_GATE_EXPECTED_CALLS = 140

# The Volcengine capability observations were frozen by the preceding canary.
# They describe adapter-normalized response fields, not the provider's raw
# wire payload.  In particular, unavailable telemetry is never synthesized.
_PROVIDER_CONTRACTS: dict[str, dict[str, str]] = {
    DEVELOPMENT_GATE_OFFICIAL_PROVIDER: {
        "request_model": DEVELOPMENT_GATE_MODEL,
        "expected_response_model": DEVELOPMENT_GATE_MODEL,
        "prompt_cache_capability": "hit_and_miss_token_counts_reported",
        "prompt_cache_requirement": "complete_accounting_for_every_call",
        "fingerprint_capability": "system_fingerprint_reported",
        "fingerprint_requirement": "nonempty_and_stable_for_every_call",
        "endpoint_requirement": "not_bound_by_this_provider_profile",
    },
    DEVELOPMENT_GATE_VOLCENGINE_PROVIDER: {
        "request_model": DEVELOPMENT_GATE_MODEL,
        "expected_response_model": DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL,
        "prompt_cache_capability": (
            "hit_and_miss_token_counts_unavailable_after_adapter_normalization"
        ),
        "prompt_cache_requirement": (
            "both_fields_null_or_omitted_after_adapter_normalization_for_every_call"
        ),
        "fingerprint_capability": (
            "system_fingerprint_unavailable_after_adapter_normalization"
        ),
        "fingerprint_requirement": (
            "null_or_omitted_after_adapter_normalization_for_every_call"
        ),
        "endpoint_requirement": "exact_normalized_endpoint",
        "endpoint_url": DEVELOPMENT_GATE_VOLCENGINE_ENDPOINT,
    },
}

NO_RESUME_NOTICE = (
    "checkpoint/resume is intentionally unsupported; an interruption abandons "
    "the attempt, whose durable ledger remains, and requires a full fresh run"
)

_EXPECTED_CONFIG = {
    "schema_version": 1,
    "status": "development-only",
    "experiment": "adaptive-entropy-scheduling",
    "worlds": [DEVELOPMENT_GATE_WORLD],
    "episode": DEVELOPMENT_GATE_EPISODE,
    "arms": DEVELOPMENT_GATE_ARMS,
    "model": DEVELOPMENT_GATE_MODEL_CONFIG,
}


class DevelopmentGateError(RuntimeError):
    """Raised when the live development gate cannot preserve its contract."""


def _expected_config(provider: str) -> dict[str, Any]:
    return {
        **_EXPECTED_CONFIG,
        "model": {**DEVELOPMENT_GATE_MODEL_CONFIG, "provider": provider},
    }


def _provider_contract_for_profile(provider: str) -> dict[str, str]:
    if provider not in _PROVIDER_CONTRACTS:
        raise DevelopmentGateError(
            "development-gate model.provider must select an audited provider profile"
        )
    return dict(_PROVIDER_CONTRACTS[provider])


def _provider_contract(config: Mapping[str, Any]) -> dict[str, str]:
    model = config.get("model")
    provider = model.get("provider") if isinstance(model, Mapping) else None
    if not isinstance(provider, str):
        raise DevelopmentGateError(
            "development-gate model.provider must select an audited provider profile"
        )
    return _provider_contract_for_profile(provider)


def _normalize_endpoint_url(value: str) -> str:
    """Normalize only equivalent trailing slashes for endpoint binding."""

    return value.rstrip("/")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_contract_metadata(
    config: Mapping[str, Any],
    credentials: ProviderCredentials,
) -> dict[str, Any]:
    """Build the public, endpoint-redacted provider contract record."""

    profile = str(config["model"]["provider"])
    contract = _provider_contract_for_profile(profile)
    normalized_endpoint = _normalize_endpoint_url(credentials.base_url)
    expected_endpoint = contract.get("endpoint_url")
    endpoint_satisfied = (
        True
        if expected_endpoint is None
        else normalized_endpoint == expected_endpoint
    )
    return {
        "profile": profile,
        "request_model_alias": contract["request_model"],
        "expected_response_model_alias": contract["expected_response_model"],
        "telemetry_scope": (
            "adapter_normalized_response_fields_not_raw_wire_payload"
        ),
        "prompt_cache_usage": {
            "capability": contract["prompt_cache_capability"],
            "requirement": contract["prompt_cache_requirement"],
        },
        "system_fingerprint": {
            "capability": contract["fingerprint_capability"],
            "requirement": contract["fingerprint_requirement"],
        },
        "endpoint": {
            "normalization": "remove_trailing_slashes_only",
            "requirement": contract["endpoint_requirement"],
            "normalized_url_sha256": _sha256_text(normalized_endpoint),
            "expected_normalized_url_sha256": (
                None
                if expected_endpoint is None
                else _sha256_text(expected_endpoint)
            ),
            "contract_satisfied": endpoint_satisfied,
        },
        "contract_satisfied": endpoint_satisfied,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AttemptLedger:
    """Durable append-only audit events for one non-resumable paid attempt."""

    __slots__ = ("attempt_id", "path", "_handle")

    def __init__(
        self,
        path: Path,
        *,
        provenance: Mapping[str, Any],
        config_sha256: str,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.attempt_id = uuid.uuid4().hex
        self._handle = path.open("x", encoding="utf-8")
        self.append(
            {
                "event": "attempt_started",
                "at_utc": _utc_now(),
                "expected_logical_calls": DEVELOPMENT_GATE_EXPECTED_CALLS,
                "attempt_id": self.attempt_id,
                "config_sha256": config_sha256,
                "source_manifest_sha256": provenance["source_manifest_sha256"],
                "model": DEVELOPMENT_GATE_MODEL,
                "thinking": "disabled",
                "seed_supported": False,
                "resume_supported": False,
            }
        )

    def append(self, event: Mapping[str, Any]) -> None:
        payload = {"schema_version": 1, "attempt_id": self.attempt_id, **dict(event)}
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        self._handle.write(encoded + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> "AttemptLedger":
        return self

    def __exit__(self, *args: Any) -> None:
        del args
        self.close()


def _config_sha256(config: Mapping[str, Any]) -> str:
    payload = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _planned_calls(config: Mapping[str, Any]) -> int:
    return (
        len(config["worlds"])
        * len(config["arms"])
        * int(config["episode"]["rounds"])
        * int(config["episode"]["candidates_per_round"])
    )


def validate_development_gate_config(
    config: Mapping[str, Any] | str | Path = DEVELOPMENT_GATE_CONFIG_PATH,
) -> dict[str, Any]:
    """Validate the generic schema and the exact one-world gate contract."""

    validated = (
        load_config(config)
        if isinstance(config, (str, Path))
        else validate_config(config)
    )
    contract = _provider_contract(validated)
    expected = _expected_config(str(validated["model"]["provider"]))
    if validated != expected:
        drifted = sorted(
            key
            for key in set(validated).union(expected)
            if validated.get(key) != expected.get(key)
        )
        fields = ", ".join(drifted) if drifted else "unknown"
        raise DevelopmentGateError(
            "development-gate config must exactly match the audited one-world "
            f"contract; drifted top-level fields: {fields}"
        )
    if validated["model"]["name"] != contract["request_model"]:
        raise DevelopmentGateError(
            "development-gate request model does not match its provider profile"
        )
    planned_calls = _planned_calls(validated)
    if planned_calls != DEVELOPMENT_GATE_EXPECTED_CALLS:
        raise DevelopmentGateError(
            "development-gate call budget must be exactly "
            f"{DEVELOPMENT_GATE_EXPECTED_CALLS}; received {planned_calls}"
        )
    return validated


def preflight_development_gate(
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = DEVELOPMENT_GATE_CONFIG_PATH,
) -> dict[str, Any]:
    """Reject configuration or credential-model drift before live setup."""

    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    validated = validate_development_gate_config(config)
    contract = _provider_contract(validated)
    configured_model = validated["model"]
    if configured_model["snapshot"] is not None:
        raise DevelopmentGateError(
            "development-gate snapshot must remain null because the provider "
            "exposes only a movable response alias"
        )
    if credentials.model != contract["request_model"]:
        raise DevelopmentGateError(
            "credential model does not match the audited development-gate model"
        )
    if (
        configured_model["provider"] == DEVELOPMENT_GATE_VOLCENGINE_PROVIDER
        and _normalize_endpoint_url(credentials.base_url)
        != contract["endpoint_url"]
    ):
        raise DevelopmentGateError(
            "credential endpoint does not match the audited Volcengine endpoint"
        )
    if not credentials.api_key:
        raise DevelopmentGateError("development-gate API credential is empty")
    return validated


class _ProgressReporter:
    """Shared, response-only progress ledger for all seven fresh generators."""

    __slots__ = (
        "count",
        "contract",
        "expected",
        "expected_response_model",
        "ledger",
        "provider_profile",
        "started",
        "stream",
        "_fingerprint_sha256",
    )

    def __init__(
        self,
        expected: int,
        stream: TextIO,
        ledger: AttemptLedger | None = None,
        *,
        provider_profile: str = DEVELOPMENT_GATE_OFFICIAL_PROVIDER,
    ) -> None:
        self.count = 0
        self.started = 0
        self.expected = expected
        self.stream = stream
        self.ledger = ledger
        self.provider_profile = provider_profile
        self.contract = _provider_contract_for_profile(provider_profile)
        self.expected_response_model = self.contract["expected_response_model"]
        self._fingerprint_sha256: str | None = None

    def start(self, kwargs: Mapping[str, Any]) -> int:
        if self.started >= self.expected:
            raise DevelopmentGateError("provider started more requests than budgeted")
        self.started += 1
        serial_index = self.started
        arm_index = (serial_index - 1) // 20
        arm_id = ARM_EXECUTION_BASE_ORDER[arm_index]
        if self.ledger is not None:
            self.ledger.append(
                {
                    "event": "logical_request_started",
                    "at_utc": _utc_now(),
                    "serial_index": serial_index,
                    "arm_id": arm_id,
                    "round_index": kwargs.get("round_index"),
                    "candidate_index": kwargs.get("candidate_index"),
                    "temperature": kwargs.get("temperature"),
                }
            )
        return serial_index

    def record(self, serial_index: int, response: GenerationResponse) -> None:
        if self.count >= self.expected or serial_index > self.started:
            raise DevelopmentGateError("provider returned more responses than budgeted")
        self.count += 1
        fingerprint = response.provider_fingerprint
        fingerprint_sha256 = (
            hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
            if isinstance(fingerprint, str) and fingerprint.strip()
            else None
        )
        violations: list[str] = []
        if response.provider_request_count != 1:
            violations.append("provider_request_count")
        if response.seed_supported is not False:
            violations.append("seed_support")
        if response.provider_model != self.expected_response_model:
            violations.append("response_model")
        if response.finish_reason != "stop":
            violations.append("finish_reason")
        if response.candidate_format not in CANDIDATE_FORMATS:
            violations.append("candidate_format_metadata")
        if self.provider_profile == DEVELOPMENT_GATE_OFFICIAL_PROVIDER:
            if (
                type(response.prompt_cache_hit_tokens) is not int
                or type(response.prompt_cache_miss_tokens) is not int
                or response.input_tokens
                != response.prompt_cache_hit_tokens
                + response.prompt_cache_miss_tokens
            ):
                violations.append("prompt_cache_accounting")
        elif (
            response.prompt_cache_hit_tokens is not None
            or response.prompt_cache_miss_tokens is not None
        ):
            violations.append("prompt_cache_unavailable_contract")
        if response.output_tokens > DEVELOPMENT_GATE_EPISODE["max_output_tokens"]:
            violations.append("output_token_cap")
        if response.reasoning_tokens not in {None, 0}:
            violations.append("reasoning_mode")
        if self.provider_profile == DEVELOPMENT_GATE_OFFICIAL_PROVIDER:
            if fingerprint_sha256 is None:
                violations.append("system_fingerprint_missing")
            elif self._fingerprint_sha256 is None:
                self._fingerprint_sha256 = fingerprint_sha256
            elif fingerprint_sha256 != self._fingerprint_sha256:
                violations.append("system_fingerprint_changed")
        elif response.provider_fingerprint is not None:
            violations.append("system_fingerprint_unavailable_contract")

        if self.ledger is not None:
            self.ledger.append(
                {
                    "event": "logical_request_succeeded",
                    "at_utc": _utc_now(),
                    "serial_index": serial_index,
                    "provider_model": response.provider_model,
                    "finish_reason": response.finish_reason,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "prompt_cache_hit_tokens": response.prompt_cache_hit_tokens,
                    "prompt_cache_miss_tokens": response.prompt_cache_miss_tokens,
                    "reasoning_tokens": response.reasoning_tokens,
                    "candidate_format": response.candidate_format,
                    "provider_fingerprint_sha256": fingerprint_sha256,
                    "latency_ms": response.latency_ms,
                    "provider_request_count": response.provider_request_count,
                }
            )
            if violations:
                self.ledger.append(
                    {
                        "event": "response_contract_failed",
                        "at_utc": _utc_now(),
                        "serial_index": serial_index,
                        "failure_categories": sorted(violations),
                    }
                )
        if violations:
            raise DevelopmentGateError(
                "provider response failed the frozen per-call contract"
            )
        # Only static text and validated numeric usage fields are emitted.  In
        # particular, prompts, expressions, raw payloads, endpoints, response
        # strings, and credentials never reach the progress channel.
        self.stream.write(
            "[development-gate] response "
            f"{self.count:03d}/{self.expected:03d} ok "
            f"input_tokens={response.input_tokens} "
            f"output_tokens={response.output_tokens} "
            f"provider_requests={response.provider_request_count}\n"
        )
        self.stream.flush()

    def fail(self, serial_index: int, exc: BaseException) -> None:
        if self.ledger is not None:
            event = {
                "event": "logical_request_failed_or_ambiguous",
                "at_utc": _utc_now(),
                "serial_index": serial_index,
                "error_type": type(exc).__name__,
            }
            # Never copy exception text. Even a nominally normalized public
            # exception can be constructed by an integration with arbitrary
            # prompt, payload, or credential text. This closed mapping emits
            # only fixed categories, plus a validated integer HTTP status.
            if isinstance(exc, HTTPStatusError):
                event["provider_failure_category"] = "http_status_error"
                if type(exc.status_code) is int and 100 <= exc.status_code <= 599:
                    event["http_status_code"] = exc.status_code
            elif isinstance(exc, UsagePayloadError):
                event["provider_failure_category"] = "usage_payload_error"
            elif isinstance(exc, ResponsePayloadError):
                event["provider_failure_category"] = "response_payload_error"
            elif isinstance(exc, TransportError):
                event["provider_failure_category"] = "transport_error"
            elif isinstance(exc, OpenAICompatibleError):
                event["provider_failure_category"] = "provider_adapter_error"
            self.ledger.append(event)


class _ProgressReportingGenerator:
    __slots__ = ("_generator", "_reporter")

    def __init__(self, generator: Any, reporter: _ProgressReporter) -> None:
        self._generator = generator
        self._reporter = reporter

    def generate(self, *args: Any, **kwargs: Any) -> GenerationResponse:
        serial_index = self._reporter.start(kwargs)
        try:
            response = self._generator.generate(*args, **kwargs)
        except BaseException as exc:
            self._reporter.fail(serial_index, exc)
            raise
        if not isinstance(response, GenerationResponse):
            error = DevelopmentGateError(
                "live generator must return a metered GenerationResponse"
            )
            self._reporter.fail(serial_index, error)
            raise error
        self._reporter.record(serial_index, response)
        return response


class ProgressReportingGeneratorFactory:
    """Wrap a generator factory and report every successful response safely."""

    __slots__ = (
        "_factory",
        "_reporter",
        "evidence",
        "evidence_reason",
        "mode",
        "provider_contract_metadata",
        "provider_profile",
    )

    def __init__(
        self,
        factory: Any,
        *,
        expected_responses: int = DEVELOPMENT_GATE_EXPECTED_CALLS,
        stream: TextIO | None = None,
        attempt_ledger: AttemptLedger | None = None,
        provider_profile: str = DEVELOPMENT_GATE_OFFICIAL_PROVIDER,
        provider_contract_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not callable(factory):
            raise TypeError("factory must be callable")
        if type(expected_responses) is not int or expected_responses < 1:
            raise ValueError("expected_responses must be a positive integer")
        _provider_contract_for_profile(provider_profile)
        self._factory = factory
        self.provider_profile = provider_profile
        self.provider_contract_metadata = (
            None
            if provider_contract_metadata is None
            else json.loads(
                json.dumps(
                    provider_contract_metadata,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            )
        )
        self._reporter = _ProgressReporter(
            expected_responses,
            sys.stderr if stream is None else stream,
            attempt_ledger,
            provider_profile=provider_profile,
        )
        # One world and a movable model alias make this an operational gate,
        # never scientific evidence, even when every accounting check passes.
        self.evidence = False
        self.evidence_reason = (
            "one-world operational gate with a movable provider alias; "
            "never confirmatory evidence"
        )
        self.mode = str(getattr(factory, "mode", "development-gate-live"))

    @property
    def successful_responses(self) -> int:
        return self._reporter.count

    @property
    def expected_responses(self) -> int:
        return self._reporter.expected

    @property
    def started_requests(self) -> int:
        return self._reporter.started

    def __call__(self, context: GeneratorContext) -> _ProgressReportingGenerator:
        return _ProgressReportingGenerator(self._factory(context), self._reporter)


def build_live_generator_factory(
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = DEVELOPMENT_GATE_CONFIG_PATH,
    progress_stream: TextIO | None = None,
    attempt_ledger: AttemptLedger | None = None,
) -> ProgressReportingGeneratorFactory:
    """Build the one-attempt, thinking-disabled live provider factory."""

    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    validated = preflight_development_gate(credentials, config=config)
    provider_profile = str(validated["model"]["provider"])
    contract = _provider_contract(validated)
    provider_factory = OpenAICompatibleGeneratorFactory(
        base_url=credentials.base_url,
        api_key=credentials.api_key,
        model=contract["request_model"],
        seed_supported=False,
        evidence=False,
        mode="development-gate-live",
        evidence_reason=(
            "one-world operational gate; model alias is not an immutable snapshot"
        ),
        timeout=60.0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return ProgressReportingGeneratorFactory(
        provider_factory,
        expected_responses=DEVELOPMENT_GATE_EXPECTED_CALLS,
        stream=progress_stream,
        attempt_ledger=attempt_ledger,
        provider_profile=provider_profile,
        provider_contract_metadata=_provider_contract_metadata(
            validated, credentials
        ),
    )


def _validate_completed_summary(
    summary: Mapping[str, Any],
    factory: ProgressReportingGeneratorFactory,
) -> None:
    """Require a complete, one-request-per-response gate before persistence."""

    try:
        budget = summary["budget"]
        planned = budget["generation_calls_planned"]
        completed = budget["generation_calls_completed"]
        provider_requests = budget["provider_requests"]
        retry_count = budget["retry_count"]
        run_count = budget["run_count"]
        usage_available = budget["actual_usage_available"]
        configured_model = summary["model"]["configured"]
        worlds = summary["worlds"]
        arms = summary["arms"]
        runs = summary["runs"]
        provider_contract_metadata = summary["provider_contract"]
    except (KeyError, TypeError) as exc:
        raise DevelopmentGateError("experiment returned an incomplete summary") from exc

    provider_profile = getattr(factory, "provider_profile", None)
    if not isinstance(provider_profile, str):
        raise DevelopmentGateError("completed summary has no audited provider profile")
    contract = _provider_contract_for_profile(provider_profile)
    expected_model_config = _expected_config(provider_profile)["model"]
    expected_contract_metadata = getattr(
        factory, "provider_contract_metadata", None
    )
    if (
        not isinstance(expected_contract_metadata, Mapping)
        or provider_contract_metadata != expected_contract_metadata
        or provider_contract_metadata.get("profile") != provider_profile
        or provider_contract_metadata.get("request_model_alias")
        != contract["request_model"]
        or provider_contract_metadata.get("expected_response_model_alias")
        != contract["expected_response_model"]
        or not isinstance(provider_contract_metadata.get("endpoint"), Mapping)
        or provider_contract_metadata["endpoint"].get("contract_satisfied")
        is not True
        or provider_contract_metadata.get("contract_satisfied") is not True
    ):
        raise DevelopmentGateError(
            "completed summary provider-contract metadata is incomplete or drifted"
        )

    exact_budget = (
        planned == DEVELOPMENT_GATE_EXPECTED_CALLS
        and completed == DEVELOPMENT_GATE_EXPECTED_CALLS
        and budget.get("max_output_tokens_planned")
        == DEVELOPMENT_GATE_EXPECTED_CALLS
        * DEVELOPMENT_GATE_EPISODE["max_output_tokens"]
        and budget.get("max_output_tokens_completed_ceiling")
        == DEVELOPMENT_GATE_EXPECTED_CALLS
        * DEVELOPMENT_GATE_EPISODE["max_output_tokens"]
        and provider_requests == DEVELOPMENT_GATE_EXPECTED_CALLS
        and retry_count == 0
        and factory.started_requests == DEVELOPMENT_GATE_EXPECTED_CALLS
        and factory.successful_responses == DEVELOPMENT_GATE_EXPECTED_CALLS
        and factory.expected_responses == DEVELOPMENT_GATE_EXPECTED_CALLS
        and run_count == len(DEVELOPMENT_GATE_ARMS)
        and usage_available is True
    )
    if not exact_budget:
        raise DevelopmentGateError(
            "completed summary does not prove exactly 140 successful one-attempt calls"
        )
    if configured_model != expected_model_config:
        raise DevelopmentGateError("completed summary model config drifted from preflight")
    if summary.get("evidence") is not False or summary.get("evidence_scope") != "non-evidence":
        raise DevelopmentGateError("one-world operational gate must remain non-evidence")
    if len(worlds) != 1 or {
        "seed": worlds[0].get("seed"),
        "depth": worlds[0].get("depth"),
    } != DEVELOPMENT_GATE_WORLD:
        raise DevelopmentGateError("completed summary does not contain the one audited world")
    if worlds[0].get("arm_execution_order") != list(ARM_EXECUTION_BASE_ORDER):
        raise DevelopmentGateError("completed summary arm execution order drifted")
    if {item.get("arm_id") for item in arms} != set(DEVELOPMENT_GATE_ARMS):
        raise DevelopmentGateError("completed summary does not contain all seven arms")
    candidates = [candidate for run in runs for candidate in run.get("candidates", ())]
    if len(candidates) != DEVELOPMENT_GATE_EXPECTED_CALLS:
        raise DevelopmentGateError("completed summary candidate ledger is not exactly 140")
    if any(
        candidate.get("candidate_format") not in CANDIDATE_FORMATS
        for candidate in candidates
    ):
        raise DevelopmentGateError(
            "completed summary must classify all 140 candidate formats"
        )
    if any(candidate.get("provider_request_count") != 1 for candidate in candidates):
        raise DevelopmentGateError("completed summary contains a retried provider response")
    if any(candidate.get("seed_supported") is not False for candidate in candidates):
        raise DevelopmentGateError("completed summary does not record seed_supported=false")
    if any(candidate.get("finish_reason") != "stop" for candidate in candidates):
        raise DevelopmentGateError("completed summary contains a non-stop finish reason")
    if any(
        candidate.get("provider_model") != contract["expected_response_model"]
        for candidate in candidates
    ):
        raise DevelopmentGateError("completed summary contains model-identity drift")
    fingerprint_values = [
        candidate.get("provider_fingerprint") for candidate in candidates
    ]
    fingerprints = {
        value for value in fingerprint_values if isinstance(value, str) and value
    }
    if provider_profile == DEVELOPMENT_GATE_OFFICIAL_PROVIDER:
        if (
            len(fingerprints) != 1
            or any(
                not isinstance(value, str) or not value.strip()
                for value in fingerprint_values
            )
        ):
            raise DevelopmentGateError(
                "completed summary must contain one stable non-empty system fingerprint"
            )
    elif any(value is not None for value in fingerprint_values):
        raise DevelopmentGateError(
            "completed summary violates the unavailable fingerprint contract"
        )
    if any(
        candidate.get(field) is None
        for candidate in candidates
        for field in ("input_tokens", "output_tokens", "latency_ms")
    ):
        raise DevelopmentGateError("completed summary has incomplete per-call usage")
    if provider_profile == DEVELOPMENT_GATE_OFFICIAL_PROVIDER:
        if any(
            type(candidate.get("prompt_cache_hit_tokens")) is not int
            or type(candidate.get("prompt_cache_miss_tokens")) is not int
            or candidate.get("input_tokens")
            != candidate.get("prompt_cache_hit_tokens")
            + candidate.get("prompt_cache_miss_tokens")
            for candidate in candidates
        ):
            raise DevelopmentGateError(
                "completed summary has incomplete or inconsistent prompt-cache accounting"
            )
    elif any(
        candidate.get("prompt_cache_hit_tokens") is not None
        or candidate.get("prompt_cache_miss_tokens") is not None
        for candidate in candidates
    ):
        raise DevelopmentGateError(
            "completed summary violates the unavailable prompt-cache contract"
        )
    run_budgets = [run.get("budget", {}) for run in runs]
    if provider_profile == DEVELOPMENT_GATE_OFFICIAL_PROVIDER:
        if (
            type(budget.get("prompt_cache_hit_tokens")) is not int
            or type(budget.get("prompt_cache_miss_tokens")) is not int
            or budget.get("actual_input_tokens")
            != budget.get("prompt_cache_hit_tokens")
            + budget.get("prompt_cache_miss_tokens")
            or any(
                type(item.get("prompt_cache_hit_tokens")) is not int
                or type(item.get("prompt_cache_miss_tokens")) is not int
                or item.get("actual_input_tokens")
                != item.get("prompt_cache_hit_tokens")
                + item.get("prompt_cache_miss_tokens")
                for item in run_budgets
            )
        ):
            raise DevelopmentGateError(
                "completed summary cache budgets drifted from the official contract"
            )
    elif (
        budget.get("prompt_cache_hit_tokens") is not None
        or budget.get("prompt_cache_miss_tokens") is not None
        or any(
            item.get("prompt_cache_hit_tokens") is not None
            or item.get("prompt_cache_miss_tokens") is not None
            for item in run_budgets
        )
    ):
        raise DevelopmentGateError(
            "completed summary cache budgets violate the unavailable contract"
        )
    if any(
        candidate.get("output_tokens") > DEVELOPMENT_GATE_EPISODE["max_output_tokens"]
        for candidate in candidates
    ):
        raise DevelopmentGateError(
            "completed summary exceeds the frozen output-token cap"
        )
    if any(
        candidate.get("reasoning_tokens") not in {None, 0}
        for candidate in candidates
    ):
        raise DevelopmentGateError(
            "provider reported reasoning tokens despite thinking being disabled"
        )
    if any(
        run.get("budget", {}).get("generation_calls_planned") != 20
        or run.get("budget", {}).get("generation_calls_completed") != 20
        or run.get("budget", {}).get("max_output_tokens_per_call")
        != DEVELOPMENT_GATE_EPISODE["max_output_tokens"]
        or run.get("budget", {}).get("max_output_tokens_planned")
        != 20 * DEVELOPMENT_GATE_EPISODE["max_output_tokens"]
        or run.get("budget", {}).get("max_output_tokens_completed_ceiling")
        != 20 * DEVELOPMENT_GATE_EPISODE["max_output_tokens"]
        or len(run.get("candidates", ())) != 20
        for run in runs
    ):
        raise DevelopmentGateError("each arm must contain exactly 20 completed slots")
    if summary["model"].get("observed_response_models") != [
        contract["expected_response_model"]
    ]:
        raise DevelopmentGateError("observed response-model ledger is not exact")
    expected_observed_fingerprints = (
        sorted(fingerprints)
        if provider_profile == DEVELOPMENT_GATE_OFFICIAL_PROVIDER
        else []
    )
    if (
        summary["model"].get("observed_system_fingerprints")
        != expected_observed_fingerprints
    ):
        raise DevelopmentGateError("observed system-fingerprint ledger is not exact")
    if summary["model"].get("finish_reason_counts") != {
        "stop": DEVELOPMENT_GATE_EXPECTED_CALLS
    }:
        raise DevelopmentGateError("finish-reason ledger is not exact")


def _secret_free_json(
    value: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DevelopmentGateError("result artifact is not finite JSON") from exc
    if any(secret and secret in encoded for secret in forbidden_values):
        raise DevelopmentGateError("refusing to persist a result containing a credential")
    return encoded + "\n"


def _write_new_json(
    path: Path,
    result: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> None:
    """Write one secret-free artifact using an exclusive create operation."""

    encoded = _secret_free_json(result, forbidden_values=forbidden_values)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)


def _require_unused_output(path: Path) -> None:
    # ``is_symlink`` also catches a broken link, for which ``exists`` is false.
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {path}")


def run_development_gate(
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = DEVELOPMENT_GATE_CONFIG_PATH,
    generator_factory: Any | None = None,
    progress_stream: TextIO | None = None,
    attempt_ledger: AttemptLedger | None = None,
    provenance_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the exact development gate without writing a partial artifact.

    ``generator_factory`` exists for offline tests.  The CLI never supplies it
    and therefore always uses :class:`OpenAICompatibleGeneratorFactory`.
    """

    validated = preflight_development_gate(credentials, config=config)
    provider_profile = str(validated["model"]["provider"])
    contract_metadata = _provider_contract_metadata(validated, credentials)
    factory = (
        build_live_generator_factory(
            credentials,
            config=validated,
            progress_stream=progress_stream,
            attempt_ledger=attempt_ledger,
        )
        if generator_factory is None
        else ProgressReportingGeneratorFactory(
            generator_factory,
            expected_responses=DEVELOPMENT_GATE_EXPECTED_CALLS,
            stream=progress_stream,
            attempt_ledger=attempt_ledger,
            provider_profile=provider_profile,
            provider_contract_metadata=contract_metadata,
        )
    )
    summary = run_experiment(validated, factory)
    summary["provider_contract"] = contract_metadata
    _validate_completed_summary(summary, factory)
    if provenance_manifest is not None:
        summary["provenance"] = json.loads(
            json.dumps(provenance_manifest, ensure_ascii=False, allow_nan=False)
        )
    _secret_free_json(
        summary,
        forbidden_values=(
            credentials.api_key,
            credentials.base_url,
            _normalize_endpoint_url(credentials.base_url),
        ),
    )
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Safety limitations:\n"
            f"  - {NO_RESUME_NOTICE}.\n"
            "  - Development-only: do not use hidden-test outcomes to tune the protocol."
        ),
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--env-prefix", default="DEEPSEEK")
    parser.add_argument("--config", type=Path, default=DEVELOPMENT_GATE_CONFIG_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="authorize exactly one fresh 140-call development-gate attempt",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="unsupported; partial attempts cannot be merged safely",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.resume:
        parser.error(
            "checkpoint/resume is not implemented safely; start one complete fresh run"
        )
    if not args.execute:
        parser.error("refusing external API use without --execute")

    ledger: AttemptLedger | None = None
    try:
        _require_unused_output(args.output)
        _require_unused_output(args.attempt_ledger)
        credentials = load_provider_credentials(
            prefix=args.env_prefix,
            env_file=args.env_file,
        )
        # Validate before constructing a transport or making request one.
        validated = preflight_development_gate(credentials, config=args.config)
        provenance = source_manifest(PROJECT_ROOT)
        ledger = AttemptLedger(
            args.attempt_ledger,
            provenance=provenance,
            config_sha256=_config_sha256(validated),
        )
        print(
            "[development-gate] preflight ok "
            f"calls={DEVELOPMENT_GATE_EXPECTED_CALLS} "
            f"model={DEVELOPMENT_GATE_MODEL} worlds=1 arms=7",
            file=sys.stderr,
            flush=True,
        )
        print(f"[development-gate] limitation: {NO_RESUME_NOTICE}", file=sys.stderr)
        print(
            "[development-gate] development-only: do not tune the protocol from "
            "hidden-test outcomes",
            file=sys.stderr,
            flush=True,
        )
        summary = run_development_gate(
            credentials,
            config=args.config,
            progress_stream=sys.stderr,
            attempt_ledger=ledger,
            provenance_manifest=provenance,
        )
        postflight_provenance = source_manifest(PROJECT_ROOT)
        if (
            postflight_provenance["source_manifest_sha256"]
            != provenance["source_manifest_sha256"]
            or postflight_provenance["environment"].get("git_head")
            != provenance["environment"].get("git_head")
        ):
            raise DevelopmentGateError(
                "protocol source tree changed during the paid attempt"
            )
        _write_new_json(
            args.output,
            summary,
            forbidden_values=(
                credentials.api_key,
                credentials.base_url,
                _normalize_endpoint_url(credentials.base_url),
            ),
        )
        ledger.append(
            {
                "event": "attempt_completed",
                "at_utc": _utc_now(),
                "successful_logical_calls": DEVELOPMENT_GATE_EXPECTED_CALLS,
                "source_manifest_sha256": provenance["source_manifest_sha256"],
                "result_artifact_written": True,
            }
        )
        ledger.close()
    except BaseException as exc:
        if ledger is not None:
            try:
                ledger.append(
                    {
                        "event": "attempt_aborted",
                        "at_utc": _utc_now(),
                        "error_type": type(exc).__name__,
                        "result_artifact_written": False,
                    }
                )
            finally:
                ledger.close()
        # Provider/transport adapters already normalize their own failures, but
        # do not interpolate any exception message here: an injected transport
        # or unexpected dependency could put request material in it.
        print(
            "[development-gate] aborted safely; no result artifact was written "
            f"(error_type={type(exc).__name__})",
            file=sys.stderr,
            flush=True,
        )
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 1

    print(
        "[development-gate] complete; 140-response result written exclusively",
        file=sys.stderr,
        flush=True,
    )
    return 0


__all__ = [
    "AttemptLedger",
    "DEVELOPMENT_GATE_ARMS",
    "DEVELOPMENT_GATE_CONFIG_PATH",
    "DEVELOPMENT_GATE_EPISODE",
    "DEVELOPMENT_GATE_EXPECTED_CALLS",
    "DEVELOPMENT_GATE_MODEL",
    "DEVELOPMENT_GATE_MODEL_CONFIG",
    "DEVELOPMENT_GATE_OFFICIAL_PROVIDER",
    "DEVELOPMENT_GATE_VOLCENGINE_CONFIG_PATH",
    "DEVELOPMENT_GATE_VOLCENGINE_ENDPOINT",
    "DEVELOPMENT_GATE_VOLCENGINE_PROVIDER",
    "DEVELOPMENT_GATE_VOLCENGINE_RESPONSE_MODEL",
    "DEVELOPMENT_GATE_WORLD",
    "DevelopmentGateError",
    "NO_RESUME_NOTICE",
    "ProgressReportingGeneratorFactory",
    "build_live_generator_factory",
    "main",
    "preflight_development_gate",
    "run_development_gate",
    "validate_development_gate_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
