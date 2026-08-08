"""Guarded live runner for the frozen eight-world development pilot.

The command executes the checked-in ``configs/pilot.json`` contract exactly
once: eight worlds, seven arms per world, and twenty model calls per run.  It
has no retry, checkpoint, or resume path.  An interrupted or failed attempt
keeps its append-only request ledger but does not produce a result artifact.

This runner is development-only.  It delegates orchestration to
``experiment.run_experiment`` so private-test evaluation remains globally
delayed until all 1,120 generation calls have completed.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, TextIO
import uuid

from .credentials import ProviderCredentials, load_provider_credentials
from .experiment import (
    GeneratorContext,
    _arm_execution_order,
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


DEVELOPMENT_PILOT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "pilot.json"
)
DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "pilot-volcengine.json"
)
DEVELOPMENT_PILOT_MODEL = "deepseek-v4-flash"
DEVELOPMENT_PILOT_OFFICIAL_PROVIDER = "deepseek-openai-compatible"
DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER = (
    "volcengine-agent-plan-openai-compatible"
)
DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL = "deepseek-v4-flash-ga-260731"
DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT = (
    "https://ark.cn-beijing.volces.com/api/plan/v3"
)
DEVELOPMENT_PILOT_WORLDS = (
    {"seed": 1001, "depth": 4},
    {"seed": 1002, "depth": 5},
    {"seed": 1003, "depth": 3},
    {"seed": 1004, "depth": 4},
    {"seed": 1005, "depth": 5},
    {"seed": 1006, "depth": 3},
    {"seed": 1007, "depth": 4},
    {"seed": 1008, "depth": 5},
)
DEVELOPMENT_PILOT_EPISODE = {
    "rounds": 5,
    "candidates_per_round": 4,
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    "archive_size": 4,
    "max_counterexamples_per_round": 2,
}
DEVELOPMENT_PILOT_ARMS = {
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
DEVELOPMENT_PILOT_MODEL_CONFIG = {
    "provider": DEVELOPMENT_PILOT_OFFICIAL_PROVIDER,
    "name": DEVELOPMENT_PILOT_MODEL,
    "snapshot": None,
    "structured_output": True,
}
DEVELOPMENT_PILOT_CALLS_PER_RUN = 20
DEVELOPMENT_PILOT_EXPECTED_RUNS = len(DEVELOPMENT_PILOT_WORLDS) * len(
    DEVELOPMENT_PILOT_ARMS
)
DEVELOPMENT_PILOT_EXPECTED_CALLS = (
    DEVELOPMENT_PILOT_EXPECTED_RUNS * DEVELOPMENT_PILOT_CALLS_PER_RUN
)
DEVELOPMENT_PILOT_MODE = "development-pilot-live"

NO_RESUME_NOTICE = (
    "checkpoint/resume is unsupported; an interrupted attempt remains only in "
    "its durable ledger and a new invocation starts all 1,120 calls again"
)

_EXPECTED_CONFIG = {
    "schema_version": 1,
    "status": "development-only",
    "experiment": "adaptive-entropy-scheduling",
    "worlds": [dict(world) for world in DEVELOPMENT_PILOT_WORLDS],
    "episode": DEVELOPMENT_PILOT_EPISODE,
    "arms": DEVELOPMENT_PILOT_ARMS,
    "model": DEVELOPMENT_PILOT_MODEL_CONFIG,
}
_PROVIDER_CONTRACTS: dict[str, dict[str, str]] = {
    DEVELOPMENT_PILOT_OFFICIAL_PROVIDER: {
        "request_model": DEVELOPMENT_PILOT_MODEL,
        "expected_response_model": DEVELOPMENT_PILOT_MODEL,
        "prompt_cache_capability": "hit_and_miss_token_counts_reported",
        "prompt_cache_requirement": "complete_accounting_for_every_call",
        "fingerprint_capability": "system_fingerprint_reported",
        "fingerprint_requirement": "nonempty_and_stable_for_every_call",
        "endpoint_binding": "runtime_credential_sha256_audit_only",
    },
    DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER: {
        "request_model": DEVELOPMENT_PILOT_MODEL,
        "expected_response_model": DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL,
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
        "endpoint_binding": "fixed_exact_after_removing_trailing_slashes",
        "endpoint_url": DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
    },
}
_FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "candidate_expression",
        "content",
        "endpoint",
        "expression",
        "prompt",
        "raw",
        "raw_content",
        "raw_prompt",
        "raw_response",
    }
)


class DevelopmentPilotError(RuntimeError):
    """Raised when the live pilot cannot preserve its frozen contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _config_sha256(config: Mapping[str, Any]) -> str:
    return _sha256_text(_canonical_json(config))


def _expected_config(provider: str) -> dict[str, Any]:
    return {
        **_EXPECTED_CONFIG,
        "model": {**DEVELOPMENT_PILOT_MODEL_CONFIG, "provider": provider},
    }


def _provider_contract(config: Mapping[str, Any]) -> dict[str, str]:
    model = config.get("model")
    provider = model.get("provider") if isinstance(model, Mapping) else None
    if provider not in _PROVIDER_CONTRACTS:
        raise DevelopmentPilotError(
            "pilot model.provider must select an audited provider profile"
        )
    return dict(_PROVIDER_CONTRACTS[str(provider)])


def _normalize_endpoint_url(value: str) -> str:
    """Normalize only equivalent trailing slashes for endpoint binding."""

    return value.rstrip("/")


def _public_provider_contract(
    config: Mapping[str, Any],
    credentials: ProviderCredentials,
) -> dict[str, Any]:
    contract = _provider_contract(config)
    profile = str(config["model"]["provider"])
    normalized_endpoint = _normalize_endpoint_url(credentials.base_url)
    endpoint_satisfied = (
        normalized_endpoint == contract["endpoint_url"]
        if profile == DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
        else bool(normalized_endpoint)
    )
    return {
        "profile": profile,
        "request_model": contract["request_model"],
        "expected_response_model": contract["expected_response_model"],
        "capability_contract": {
            "prompt_cache_usage": {
                "observed_capability": contract["prompt_cache_capability"],
                "pass_requirement": contract["prompt_cache_requirement"],
            },
            "system_fingerprint": {
                "observed_capability": contract["fingerprint_capability"],
                "pass_requirement": contract["fingerprint_requirement"],
            },
        },
        "endpoint_contract": {
            "binding": contract["endpoint_binding"],
            "normalization": "remove_trailing_slashes_only",
            "normalized_url_sha256": _sha256_text(normalized_endpoint),
            "contract_satisfied": endpoint_satisfied,
        },
        "contract_satisfied": endpoint_satisfied,
    }


def _planned_calls(config: Mapping[str, Any]) -> int:
    return (
        len(config["worlds"])
        * len(config["arms"])
        * int(config["episode"]["rounds"])
        * int(config["episode"]["candidates_per_round"])
    )


def validate_development_pilot_config(
    config: Mapping[str, Any] | str | Path = DEVELOPMENT_PILOT_CONFIG_PATH,
) -> dict[str, Any]:
    """Validate the generic schema and exact checked-in pilot contract."""

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
        raise DevelopmentPilotError(
            "pilot config must exactly match the frozen eight-world contract; "
            f"drifted top-level fields: {fields}"
        )
    if validated["model"]["name"] != contract["request_model"]:
        raise DevelopmentPilotError(
            "pilot request model does not match its provider profile"
        )
    if _planned_calls(validated) != DEVELOPMENT_PILOT_EXPECTED_CALLS:
        raise DevelopmentPilotError("pilot call budget must be exactly 1,120")
    return validated


def preflight_development_pilot(
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = DEVELOPMENT_PILOT_CONFIG_PATH,
) -> dict[str, Any]:
    """Reject credential, model, or protocol drift before live setup."""

    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    validated = validate_development_pilot_config(config)
    contract = _provider_contract(validated)
    if validated["model"]["snapshot"] is not None:
        raise DevelopmentPilotError("pilot model snapshot must remain null")
    if credentials.model != contract["request_model"]:
        raise DevelopmentPilotError(
            "credential model does not match the frozen development-pilot model"
        )
    if (
        validated["model"]["provider"]
        == DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER
        and _normalize_endpoint_url(credentials.base_url)
        != contract["endpoint_url"]
    ):
        raise DevelopmentPilotError(
            "credential endpoint does not match the audited Volcengine endpoint"
        )
    if not credentials.api_key:
        raise DevelopmentPilotError("development-pilot API credential is empty")
    return validated


def _reject_sensitive_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _FORBIDDEN_RESULT_KEYS:
                raise DevelopmentPilotError(
                    "refusing to persist raw request, response, endpoint, or credential fields"
                )
            _reject_sensitive_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive_fields(item)


def _secret_free_json(
    value: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
    indent: int | None = 2,
) -> str:
    _reject_sensitive_fields(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            indent=indent,
            sort_keys=indent is None,
            separators=(",", ":") if indent is None else None,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DevelopmentPilotError("pilot audit data is not finite JSON") from exc
    if any(secret and secret in encoded for secret in forbidden_values):
        raise DevelopmentPilotError(
            "refusing to persist a result containing a credential"
        )
    return encoded + "\n"


def _failure_event(exc: BaseException) -> dict[str, Any]:
    event: dict[str, Any] = {"provider_failure_category": "unclassified_error"}
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
    return event


class AttemptLedger:
    """Durable append-only events for one non-resumable paid attempt."""

    __slots__ = ("attempt_id", "path", "_handle")

    def __init__(
        self,
        path: Path,
        *,
        provenance: Mapping[str, Any],
        config_sha256: str,
        provider_contract: Mapping[str, Any] | None = None,
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.attempt_id = uuid.uuid4().hex
        self._handle = path.open("x", encoding="utf-8")
        started_event: dict[str, Any] = {
            "event": "attempt_started",
            "at_utc": _utc_now(),
            "expected_logical_calls": DEVELOPMENT_PILOT_EXPECTED_CALLS,
            "expected_runs": DEVELOPMENT_PILOT_EXPECTED_RUNS,
            "config_sha256": config_sha256,
            "source_manifest_sha256": provenance["source_manifest_sha256"],
            "model": DEVELOPMENT_PILOT_MODEL,
            "thinking": "disabled",
            "seed_supported": False,
            "retry_supported": False,
            "resume_supported": False,
        }
        if provider_contract is not None:
            started_event["provider_contract"] = json.loads(
                _canonical_json(provider_contract)
            )
        self.append(started_event)

    @property
    def closed(self) -> bool:
        return self._handle.closed

    def append(self, event: Mapping[str, Any]) -> None:
        payload = {"schema_version": 1, "attempt_id": self.attempt_id, **dict(event)}
        encoded = _secret_free_json(payload, indent=None)
        self._handle.write(encoded)
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


def _request_coordinates(serial_index: int) -> dict[str, Any]:
    """Map one global request index through the frozen cyclic arm order."""

    if (
        type(serial_index) is not int
        or not 1 <= serial_index <= DEVELOPMENT_PILOT_EXPECTED_CALLS
    ):
        raise DevelopmentPilotError("request serial index is outside the pilot budget")
    zero_index = serial_index - 1
    run_index, slot_index = divmod(zero_index, DEVELOPMENT_PILOT_CALLS_PER_RUN)
    world_index, arm_position = divmod(run_index, len(DEVELOPMENT_PILOT_ARMS))
    order = _arm_execution_order(DEVELOPMENT_PILOT_ARMS, world_index)
    round_index, candidate_index = divmod(
        slot_index, DEVELOPMENT_PILOT_EPISODE["candidates_per_round"]
    )
    return {
        "serial_index": serial_index,
        "run_index": run_index,
        "run_slot_index": slot_index,
        "world_index": world_index,
        "world_seed": DEVELOPMENT_PILOT_WORLDS[world_index]["seed"],
        "world_depth": DEVELOPMENT_PILOT_WORLDS[world_index]["depth"],
        "arm_position": arm_position,
        "arm_id": order[arm_position],
        "round_index": round_index,
        "candidate_index": candidate_index,
    }


class _ProgressReporter:
    __slots__ = (
        "_contract",
        "_fingerprint_sha256",
        "_provider_profile",
        "count",
        "expected",
        "ledger",
        "started",
        "stream",
    )

    def __init__(
        self,
        expected: int,
        stream: TextIO,
        ledger: AttemptLedger | None,
        *,
        provider_profile: str = DEVELOPMENT_PILOT_OFFICIAL_PROVIDER,
    ) -> None:
        if provider_profile not in _PROVIDER_CONTRACTS:
            raise DevelopmentPilotError("unknown provider profile for pilot reporter")
        self.count = 0
        self.started = 0
        self.expected = expected
        self.stream = stream
        self.ledger = ledger
        self._provider_profile = provider_profile
        self._contract = dict(_PROVIDER_CONTRACTS[provider_profile])
        self._fingerprint_sha256: str | None = None

    def start(self, kwargs: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.started >= self.expected:
            raise DevelopmentPilotError("provider started more requests than budgeted")
        serial_index = self.started + 1
        coordinates = _request_coordinates(serial_index)
        if (
            kwargs.get("round_index") != coordinates["round_index"]
            or kwargs.get("candidate_index") != coordinates["candidate_index"]
        ):
            raise DevelopmentPilotError(
                "runner request order drifted from the frozen 20-call run layout"
            )
        temperature = kwargs.get("temperature")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(float(temperature))
        ):
            raise DevelopmentPilotError("runner supplied an invalid temperature")
        if kwargs.get("max_output_tokens") != DEVELOPMENT_PILOT_EPISODE[
            "max_output_tokens"
        ]:
            raise DevelopmentPilotError("runner output-token cap drifted")
        self.started = serial_index
        if self.ledger is not None:
            self.ledger.append(
                {
                    "event": "logical_request_started",
                    "at_utc": _utc_now(),
                    **coordinates,
                    "temperature": float(temperature),
                }
            )
        return serial_index, coordinates

    def record(
        self,
        serial_index: int,
        coordinates: Mapping[str, Any],
        response: GenerationResponse,
    ) -> None:
        if serial_index != self.count + 1 or serial_index > self.started:
            raise DevelopmentPilotError("provider response order drifted")
        self.count += 1
        fingerprint = response.provider_fingerprint
        fingerprint_sha256 = (
            _sha256_text(fingerprint)
            if isinstance(fingerprint, str) and fingerprint.strip()
            else None
        )
        cache_hit = response.prompt_cache_hit_tokens
        cache_miss = response.prompt_cache_miss_tokens
        if self._provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER:
            cache_contract_satisfied = (
                _nonnegative_integer(cache_hit)
                and _nonnegative_integer(cache_miss)
                and response.input_tokens == cache_hit + cache_miss
            )
            fingerprint_contract_satisfied = fingerprint_sha256 is not None
        else:
            cache_contract_satisfied = cache_hit is None and cache_miss is None
            fingerprint_contract_satisfied = response.provider_fingerprint is None
        if self.ledger is not None:
            self.ledger.append(
                {
                    "event": "logical_request_succeeded",
                    "at_utc": _utc_now(),
                    **coordinates,
                    "response_model_matches_expected": (
                        response.provider_model
                        == self._contract["expected_response_model"]
                    ),
                    "provider_profile": self._provider_profile,
                    "expected_response_model": self._contract[
                        "expected_response_model"
                    ],
                    "finish_reason_is_stop": response.finish_reason == "stop",
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "prompt_cache_hit_tokens": response.prompt_cache_hit_tokens,
                    "prompt_cache_miss_tokens": response.prompt_cache_miss_tokens,
                    "reasoning_tokens": response.reasoning_tokens,
                    "candidate_format": response.candidate_format,
                    "provider_fingerprint_sha256": fingerprint_sha256,
                    "prompt_cache_contract_satisfied": cache_contract_satisfied,
                    "system_fingerprint_contract_satisfied": (
                        fingerprint_contract_satisfied
                    ),
                    "latency_ms": response.latency_ms,
                    "provider_request_count": response.provider_request_count,
                    "seed_supported": response.seed_supported,
                }
            )
        contract_failures: list[str] = []
        if response.provider_model != self._contract["expected_response_model"]:
            contract_failures.append("response_model_drift")
        if response.finish_reason != "stop":
            contract_failures.append("finish_reason_drift")
        if response.provider_request_count != 1:
            contract_failures.append("provider_retry_detected")
        if response.seed_supported is not False:
            contract_failures.append("seed_support_drift")
        if response.candidate_format not in CANDIDATE_FORMATS:
            contract_failures.append("candidate_format_metadata_invalid")
        if not cache_contract_satisfied:
            contract_failures.append("cache_accounting_invalid")
        if response.output_tokens > DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]:
            contract_failures.append("output_token_cap_exceeded")
        if response.reasoning_tokens not in {None, 0}:
            contract_failures.append("reasoning_tokens_nonzero")
        if self._provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER:
            if not fingerprint_contract_satisfied:
                contract_failures.append("provider_fingerprint_missing")
            elif (
                self._fingerprint_sha256 is not None
                and fingerprint_sha256 != self._fingerprint_sha256
            ):
                contract_failures.append("provider_fingerprint_drift")
            if self._fingerprint_sha256 is None and fingerprint_sha256 is not None:
                self._fingerprint_sha256 = fingerprint_sha256
        elif not fingerprint_contract_satisfied:
            contract_failures.append("provider_fingerprint_unexpected")
        if contract_failures:
            if self.ledger is not None:
                self.ledger.append(
                    {
                        "event": "response_contract_failed",
                        "at_utc": _utc_now(),
                        **coordinates,
                        "failure_categories": sorted(contract_failures),
                    }
                )
            raise DevelopmentPilotError("provider response contract failed")
        self.stream.write(
            "[development-pilot] response "
            f"{self.count:04d}/{self.expected:04d} ok "
            f"world={coordinates['world_index'] + 1}/8 "
            f"arm={coordinates['arm_id']} "
            f"input_tokens={response.input_tokens} "
            f"output_tokens={response.output_tokens} "
            f"provider_requests={response.provider_request_count}\n"
        )
        self.stream.flush()

    def fail(
        self,
        serial_index: int,
        coordinates: Mapping[str, Any],
        exc: BaseException,
    ) -> None:
        if self.ledger is not None:
            self.ledger.append(
                {
                    "event": "logical_request_failed_or_ambiguous",
                    "at_utc": _utc_now(),
                    **coordinates,
                    **_failure_event(exc),
                }
            )


class _ProgressReportingGenerator:
    __slots__ = ("_generator", "_reporter")

    def __init__(self, generator: Any, reporter: _ProgressReporter) -> None:
        self._generator = generator
        self._reporter = reporter

    def generate(self, *args: Any, **kwargs: Any) -> GenerationResponse:
        serial_index, coordinates = self._reporter.start(kwargs)
        try:
            response = self._generator.generate(*args, **kwargs)
        except BaseException as exc:
            self._reporter.fail(serial_index, coordinates, exc)
            raise
        if not isinstance(response, GenerationResponse):
            error = DevelopmentPilotError(
                "live generator must return a metered GenerationResponse"
            )
            self._reporter.fail(serial_index, coordinates, error)
            raise error
        self._reporter.record(serial_index, coordinates, response)
        return response


class ProgressReportingGeneratorFactory:
    """Wrap all 56 generators with one globally indexed safe ledger."""

    __slots__ = (
        "_factory",
        "_generators_created",
        "_reporter",
        "evidence",
        "evidence_reason",
        "mode",
        "provider_profile",
    )

    def __init__(
        self,
        factory: Any,
        *,
        expected_responses: int = DEVELOPMENT_PILOT_EXPECTED_CALLS,
        stream: TextIO | None = None,
        attempt_ledger: AttemptLedger | None = None,
        provider_profile: str = DEVELOPMENT_PILOT_OFFICIAL_PROVIDER,
    ) -> None:
        if not callable(factory):
            raise TypeError("factory must be callable")
        if type(expected_responses) is not int or expected_responses < 1:
            raise ValueError("expected_responses must be a positive integer")
        self._factory = factory
        self._generators_created = 0
        self._reporter = _ProgressReporter(
            expected_responses,
            sys.stderr if stream is None else stream,
            attempt_ledger,
            provider_profile=provider_profile,
        )
        self.provider_profile = provider_profile
        self.evidence = False
        self.evidence_reason = (
            "eight-world development pilot with a movable provider alias; "
            "never confirmatory evidence"
        )
        self.mode = DEVELOPMENT_PILOT_MODE

    @property
    def successful_responses(self) -> int:
        return self._reporter.count

    @property
    def started_requests(self) -> int:
        return self._reporter.started

    @property
    def expected_responses(self) -> int:
        return self._reporter.expected

    @property
    def generators_created(self) -> int:
        return self._generators_created

    def __call__(self, context: GeneratorContext) -> _ProgressReportingGenerator:
        if self._generators_created >= DEVELOPMENT_PILOT_EXPECTED_RUNS:
            raise DevelopmentPilotError("runner created more generators than budgeted")
        generator = self._factory(context)
        self._generators_created += 1
        return _ProgressReportingGenerator(generator, self._reporter)


def build_live_generator_factory(
    credentials: ProviderCredentials,
    *,
    progress_stream: TextIO | None = None,
    attempt_ledger: AttemptLedger | None = None,
    provider_profile: str = DEVELOPMENT_PILOT_OFFICIAL_PROVIDER,
) -> ProgressReportingGeneratorFactory:
    """Build the one-shot, thinking-disabled DeepSeek provider factory."""

    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    if provider_profile not in _PROVIDER_CONTRACTS:
        raise DevelopmentPilotError("unknown provider profile for pilot factory")
    contract = _PROVIDER_CONTRACTS[provider_profile]
    provider_factory = OpenAICompatibleGeneratorFactory(
        base_url=credentials.base_url,
        api_key=credentials.api_key,
        model=contract["request_model"],
        seed_supported=False,
        evidence=False,
        mode=DEVELOPMENT_PILOT_MODE,
        evidence_reason=(
            "eight-world development pilot; model alias is not an immutable snapshot"
        ),
        timeout=60.0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    return ProgressReportingGeneratorFactory(
        provider_factory,
        expected_responses=DEVELOPMENT_PILOT_EXPECTED_CALLS,
        stream=progress_stream,
        attempt_ledger=attempt_ledger,
        provider_profile=provider_profile,
    )


def _nonnegative_integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _nonnegative_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _validate_run_usage(
    run: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    *,
    run_index: int,
    provider_profile: str = DEVELOPMENT_PILOT_OFFICIAL_PROVIDER,
) -> None:
    budget = run.get("budget")
    if not isinstance(budget, Mapping):
        raise DevelopmentPilotError("completed summary has a malformed run budget")
    expected_integer_totals = {
        "actual_input_tokens": sum(int(item["input_tokens"]) for item in candidates),
        "actual_output_tokens": sum(int(item["output_tokens"]) for item in candidates),
        "actual_billed_tokens": sum(
            int(item["input_tokens"]) + int(item["output_tokens"])
            for item in candidates
        ),
        "provider_requests": DEVELOPMENT_PILOT_CALLS_PER_RUN,
        "retry_count": 0,
    }
    exact_run = (
        budget.get("generation_calls_planned") == DEVELOPMENT_PILOT_CALLS_PER_RUN
        and budget.get("generation_calls_completed") == DEVELOPMENT_PILOT_CALLS_PER_RUN
        and budget.get("max_output_tokens_per_call")
        == DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]
        and budget.get("max_output_tokens_planned")
        == DEVELOPMENT_PILOT_CALLS_PER_RUN
        * DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]
        and budget.get("max_output_tokens_completed_ceiling")
        == DEVELOPMENT_PILOT_CALLS_PER_RUN
        * DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]
        and budget.get("actual_usage_available") is True
        and all(budget.get(key) == value for key, value in expected_integer_totals.items())
        and _nonnegative_number(budget.get("latency_ms_total"))
        and _nonnegative_number(budget.get("latency_ms_mean"))
        and math.isclose(
            float(budget.get("latency_ms_total")),
            sum(float(item["latency_ms"]) for item in candidates),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        and math.isclose(
            float(budget.get("latency_ms_mean")),
            sum(float(item["latency_ms"]) for item in candidates)
            / DEVELOPMENT_PILOT_CALLS_PER_RUN,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    )
    if not exact_run:
        raise DevelopmentPilotError(
            f"run {run_index} does not prove twenty complete one-attempt calls"
        )
    if provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER:
        expected_hit = sum(
            int(item["prompt_cache_hit_tokens"]) for item in candidates
        )
        expected_miss = sum(
            int(item["prompt_cache_miss_tokens"]) for item in candidates
        )
        if (
            budget.get("prompt_cache_hit_tokens") != expected_hit
            or budget.get("prompt_cache_miss_tokens") != expected_miss
        ):
            raise DevelopmentPilotError("run cache accounting drifted")
    elif (
        budget.get("prompt_cache_hit_tokens") is not None
        or budget.get("prompt_cache_miss_tokens") is not None
    ):
        raise DevelopmentPilotError(
            "run cache telemetry violates the Volcengine capability contract"
        )
    if budget.get("reasoning_tokens") not in {None, 0}:
        raise DevelopmentPilotError("run budget reports reasoning-token drift")
    if (
        budget.get("final_test_points_planned") in {None, 0}
        or budget.get("final_test_points_evaluated")
        != budget.get("final_test_points_planned")
    ):
        raise DevelopmentPilotError("completed run lacks its delayed private-test result")


def _validate_completed_summary(
    summary: Mapping[str, Any],
    factory: ProgressReportingGeneratorFactory,
) -> str | None:
    """Require an exact 1,120-response pilot and return its fingerprint if exposed."""

    try:
        budget = summary["budget"]
        configured_model = summary["model"]["configured"]
        worlds = summary["worlds"]
        arms = summary["arms"]
        runs = summary["runs"]
    except (KeyError, TypeError) as exc:
        raise DevelopmentPilotError("experiment returned an incomplete summary") from exc
    if not isinstance(configured_model, Mapping):
        raise DevelopmentPilotError("completed summary model config is malformed")
    provider_profile = configured_model.get("provider")
    if provider_profile not in _PROVIDER_CONTRACTS:
        raise DevelopmentPilotError("completed summary provider profile is unaudited")
    contract = _PROVIDER_CONTRACTS[str(provider_profile)]
    expected_config = _expected_config(str(provider_profile))
    factory_profile = getattr(factory, "provider_profile", provider_profile)
    if factory_profile != provider_profile:
        raise DevelopmentPilotError("runner provider profile drifted")

    exact_budget = (
        budget.get("generation_calls_planned") == DEVELOPMENT_PILOT_EXPECTED_CALLS
        and budget.get("generation_calls_completed") == DEVELOPMENT_PILOT_EXPECTED_CALLS
        and budget.get("max_output_tokens_planned")
        == DEVELOPMENT_PILOT_EXPECTED_CALLS
        * DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]
        and budget.get("max_output_tokens_completed_ceiling")
        == DEVELOPMENT_PILOT_EXPECTED_CALLS
        * DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]
        and budget.get("provider_requests") == DEVELOPMENT_PILOT_EXPECTED_CALLS
        and budget.get("retry_count") == 0
        and budget.get("run_count") == DEVELOPMENT_PILOT_EXPECTED_RUNS
        and budget.get("actual_usage_available") is True
        and factory.started_requests == DEVELOPMENT_PILOT_EXPECTED_CALLS
        and factory.successful_responses == DEVELOPMENT_PILOT_EXPECTED_CALLS
        and factory.expected_responses == DEVELOPMENT_PILOT_EXPECTED_CALLS
        and factory.generators_created == DEVELOPMENT_PILOT_EXPECTED_RUNS
    )
    if not exact_budget:
        raise DevelopmentPilotError(
            "completed summary does not prove exactly 1,120 successful one-attempt calls"
        )
    if summary.get("schema_version") != 1:
        raise DevelopmentPilotError("completed summary schema version drifted")
    if summary.get("experiment") != expected_config["experiment"]:
        raise DevelopmentPilotError("completed summary experiment identity drifted")
    if summary.get("config_status") != "development-only":
        raise DevelopmentPilotError("completed summary status drifted")
    if summary.get("config_hash") != _config_sha256(expected_config):
        raise DevelopmentPilotError("completed summary config hash drifted")
    if configured_model != expected_config["model"]:
        raise DevelopmentPilotError("completed summary model config drifted")
    if (
        summary.get("mode") != DEVELOPMENT_PILOT_MODE
        or summary.get("evidence") is not False
        or summary.get("evidence_scope") != "non-evidence"
    ):
        raise DevelopmentPilotError(
            "development pilot must remain development-pilot-live non-evidence"
        )
    if not isinstance(worlds, Sequence) or len(worlds) != len(DEVELOPMENT_PILOT_WORLDS):
        raise DevelopmentPilotError("completed summary world ledger is not exact")
    if not isinstance(arms, Sequence) or {
        item.get("arm_id") for item in arms if isinstance(item, Mapping)
    } != set(DEVELOPMENT_PILOT_ARMS):
        raise DevelopmentPilotError("completed summary arm ledger is not exact")
    if not isinstance(runs, Sequence) or len(runs) != DEVELOPMENT_PILOT_EXPECTED_RUNS:
        raise DevelopmentPilotError("completed summary run ledger is not exact")

    for world_index, expected_world in enumerate(DEVELOPMENT_PILOT_WORLDS):
        world = worlds[world_index]
        if not isinstance(world, Mapping):
            raise DevelopmentPilotError("completed summary world ledger is malformed")
        expected_order = list(_arm_execution_order(DEVELOPMENT_PILOT_ARMS, world_index))
        if (
            world.get("index") != world_index
            or world.get("seed") != expected_world["seed"]
            or world.get("depth") != expected_world["depth"]
            or world.get("arm_execution_order") != expected_order
        ):
            raise DevelopmentPilotError("completed summary world/order ledger drifted")

    all_candidates: list[Mapping[str, Any]] = []
    fingerprints: set[str] = set()
    totals = {
        "actual_input_tokens": 0,
        "actual_output_tokens": 0,
        "actual_billed_tokens": 0,
        "latency_ms_total": 0.0,
    }
    cache_totals = {"prompt_cache_hit_tokens": 0, "prompt_cache_miss_tokens": 0}
    for run_index, run in enumerate(runs):
        if not isinstance(run, Mapping):
            raise DevelopmentPilotError("completed summary run ledger is malformed")
        world_index, arm_position = divmod(run_index, len(DEVELOPMENT_PILOT_ARMS))
        expected_world = DEVELOPMENT_PILOT_WORLDS[world_index]
        expected_arm = _arm_execution_order(DEVELOPMENT_PILOT_ARMS, world_index)[
            arm_position
        ]
        world = run.get("world")
        if not isinstance(world, Mapping) or (
            run.get("arm_id") != expected_arm
            or world.get("index") != world_index
            or world.get("seed") != expected_world["seed"]
            or world.get("depth") != expected_world["depth"]
        ):
            raise DevelopmentPilotError("completed summary run rotation drifted")
        candidates = run.get("candidates")
        if (
            not isinstance(candidates, Sequence)
            or isinstance(candidates, (str, bytes))
            or len(candidates) != DEVELOPMENT_PILOT_CALLS_PER_RUN
            or any(not isinstance(item, Mapping) for item in candidates)
        ):
            raise DevelopmentPilotError("each run must contain exactly twenty candidates")
        typed_candidates = list(candidates)
        for slot_index, candidate in enumerate(typed_candidates):
            expected_round, expected_candidate = divmod(
                slot_index, DEVELOPMENT_PILOT_EPISODE["candidates_per_round"]
            )
            if (
                candidate.get("round_index") != expected_round
                or candidate.get("candidate_index") != expected_candidate
            ):
                raise DevelopmentPilotError("candidate slot order drifted")
            if candidate.get("candidate_format") not in CANDIDATE_FORMATS:
                raise DevelopmentPilotError("candidate format metadata is not closed")
            if candidate.get("provider_request_count") != 1:
                raise DevelopmentPilotError("candidate response contains a retry")
            if candidate.get("seed_supported") is not False:
                raise DevelopmentPilotError("candidate does not record seed_supported=false")
            if candidate.get("provider_model") != contract["expected_response_model"]:
                raise DevelopmentPilotError("candidate response-model identity drifted")
            if candidate.get("finish_reason") != "stop":
                raise DevelopmentPilotError("candidate finish reason is not stop")
            fingerprint = candidate.get("provider_fingerprint")
            if provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER:
                if not isinstance(fingerprint, str) or not fingerprint.strip():
                    raise DevelopmentPilotError("candidate has no system fingerprint")
                fingerprints.add(fingerprint)
            elif fingerprint is not None:
                raise DevelopmentPilotError(
                    "candidate unexpectedly exposes a system fingerprint"
                )
            for field in ("input_tokens", "output_tokens"):
                if not _nonnegative_integer(candidate.get(field)):
                    raise DevelopmentPilotError("candidate has incomplete token usage")
            if not _nonnegative_number(candidate.get("latency_ms")):
                raise DevelopmentPilotError("candidate has incomplete latency usage")
            if provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER:
                for field in (
                    "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens",
                ):
                    if not _nonnegative_integer(candidate.get(field)):
                        raise DevelopmentPilotError(
                            "candidate has incomplete cache-token usage"
                        )
                if candidate["input_tokens"] != (
                    candidate["prompt_cache_hit_tokens"]
                    + candidate["prompt_cache_miss_tokens"]
                ):
                    raise DevelopmentPilotError(
                        "candidate cache accounting is inconsistent"
                    )
            elif (
                candidate.get("prompt_cache_hit_tokens") is not None
                or candidate.get("prompt_cache_miss_tokens") is not None
            ):
                raise DevelopmentPilotError(
                    "candidate cache telemetry violates the Volcengine contract"
                )
            if candidate["output_tokens"] > DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]:
                raise DevelopmentPilotError("candidate exceeds the output-token cap")
            if candidate.get("reasoning_tokens") not in {None, 0}:
                raise DevelopmentPilotError(
                    "candidate reports reasoning tokens with thinking disabled"
                )
        _validate_run_usage(
            run,
            typed_candidates,
            run_index=run_index,
            provider_profile=str(provider_profile),
        )
        final_test = run.get("final_test")
        if not isinstance(final_test, Mapping) or final_test.get("evaluated") is not True:
            raise DevelopmentPilotError("completed run lacks delayed private-test evaluation")
        all_candidates.extend(typed_candidates)
        totals["actual_input_tokens"] += sum(
            int(item["input_tokens"]) for item in typed_candidates
        )
        totals["actual_output_tokens"] += sum(
            int(item["output_tokens"]) for item in typed_candidates
        )
        totals["actual_billed_tokens"] += sum(
            int(item["input_tokens"]) + int(item["output_tokens"])
            for item in typed_candidates
        )
        totals["latency_ms_total"] += sum(
            float(item["latency_ms"]) for item in typed_candidates
        )
        if provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER:
            cache_totals["prompt_cache_hit_tokens"] += sum(
                int(item["prompt_cache_hit_tokens"]) for item in typed_candidates
            )
            cache_totals["prompt_cache_miss_tokens"] += sum(
                int(item["prompt_cache_miss_tokens"]) for item in typed_candidates
            )

    if len(all_candidates) != DEVELOPMENT_PILOT_EXPECTED_CALLS:
        raise DevelopmentPilotError("global candidate ledger is not exactly 1,120")
    if provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER and len(fingerprints) != 1:
        raise DevelopmentPilotError(
            "completed pilot must contain one stable non-empty system fingerprint"
        )
    if provider_profile == DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER and fingerprints:
        raise DevelopmentPilotError("Volcengine fingerprint ledger must remain empty")
    integer_totals = {
        key: value for key, value in totals.items() if key != "latency_ms_total"
    }
    if any(budget.get(key) != value for key, value in integer_totals.items()):
        raise DevelopmentPilotError("aggregate token/cache accounting drifted")
    if not _nonnegative_number(budget.get("latency_ms_total")) or not math.isclose(
        float(budget.get("latency_ms_total")),
        totals["latency_ms_total"],
        rel_tol=0.0,
        abs_tol=1e-8,
    ):
        raise DevelopmentPilotError("aggregate latency accounting drifted")
    if provider_profile == DEVELOPMENT_PILOT_OFFICIAL_PROVIDER:
        if any(budget.get(key) != value for key, value in cache_totals.items()):
            raise DevelopmentPilotError("aggregate cache accounting drifted")
    elif (
        budget.get("prompt_cache_hit_tokens") is not None
        or budget.get("prompt_cache_miss_tokens") is not None
    ):
        raise DevelopmentPilotError(
            "aggregate cache telemetry violates the Volcengine contract"
        )
    if budget.get("reasoning_tokens") not in {None, 0}:
        raise DevelopmentPilotError("aggregate reasoning-token accounting drifted")

    raw_fingerprint = next(iter(fingerprints)) if fingerprints else None
    model = summary["model"]
    if model.get("observed_response_models") != [
        contract["expected_response_model"]
    ]:
        raise DevelopmentPilotError("observed response-model ledger drifted")
    expected_fingerprints = [] if raw_fingerprint is None else [raw_fingerprint]
    if model.get("observed_system_fingerprints") != expected_fingerprints:
        raise DevelopmentPilotError("observed system-fingerprint ledger drifted")
    if model.get("finish_reason_counts") != {
        "stop": DEVELOPMENT_PILOT_EXPECTED_CALLS
    }:
        raise DevelopmentPilotError("finish-reason ledger drifted")
    return raw_fingerprint


def _copy_provenance(provenance: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(provenance, Mapping):
        raise DevelopmentPilotError("source provenance manifest is required")
    digest = provenance.get("source_manifest_sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest.lower())
    ):
        raise DevelopmentPilotError("source provenance manifest hash is malformed")
    try:
        return json.loads(_canonical_json(provenance))
    except (TypeError, ValueError) as exc:
        raise DevelopmentPilotError("source provenance manifest is not finite JSON") from exc


def _sanitize_summary(
    summary: Mapping[str, Any],
    *,
    raw_fingerprint: str | None,
    provenance_manifest: Mapping[str, Any],
    provider_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Drop candidate content and retain only safe provider-contract metadata."""

    try:
        result = json.loads(_canonical_json(summary))
    except (TypeError, ValueError) as exc:
        raise DevelopmentPilotError("completed summary is not finite JSON") from exc
    fingerprint_hash = (
        None
        if raw_fingerprint is None
        else f"sha256:{_sha256_text(raw_fingerprint)}"
    )
    for run in result["runs"]:
        for candidate in run["candidates"]:
            candidate.pop("candidate_expression", None)
            candidate["provider_fingerprint"] = fingerprint_hash
    result["model"]["observed_system_fingerprints"] = (
        [] if fingerprint_hash is None else [fingerprint_hash]
    )
    if provider_contract is not None:
        result["provider_contract"] = json.loads(_canonical_json(provider_contract))
    result["execution_contract"] = {
        "logical_calls": DEVELOPMENT_PILOT_EXPECTED_CALLS,
        "runs": DEVELOPMENT_PILOT_EXPECTED_RUNS,
        "max_output_tokens_per_call": DEVELOPMENT_PILOT_EPISODE[
            "max_output_tokens"
        ],
        "provider_retries": 0,
        "resume_supported": False,
        "private_test_evaluation": (
            "globally_delayed_until_all_generation_calls_completed"
        ),
    }
    result["provenance"] = _copy_provenance(provenance_manifest)
    _reject_sensitive_fields(result)
    return result


def run_development_pilot(
    credentials: ProviderCredentials,
    *,
    provenance_manifest: Mapping[str, Any],
    config: Mapping[str, Any] | str | Path = DEVELOPMENT_PILOT_CONFIG_PATH,
    generator_factory: Any | None = None,
    progress_stream: TextIO | None = None,
    attempt_ledger: AttemptLedger | None = None,
) -> dict[str, Any]:
    """Execute the exact pilot through ``run_experiment`` and sanitize output."""

    validated = preflight_development_pilot(credentials, config=config)
    provider_profile = str(validated["model"]["provider"])
    factory = (
        build_live_generator_factory(
            credentials,
            progress_stream=progress_stream,
            attempt_ledger=attempt_ledger,
            provider_profile=provider_profile,
        )
        if generator_factory is None
        else ProgressReportingGeneratorFactory(
            generator_factory,
            expected_responses=DEVELOPMENT_PILOT_EXPECTED_CALLS,
            stream=progress_stream,
            attempt_ledger=attempt_ledger,
            provider_profile=provider_profile,
        )
    )
    summary = run_experiment(validated, factory)
    raw_fingerprint = _validate_completed_summary(summary, factory)
    result = _sanitize_summary(
        summary,
        raw_fingerprint=raw_fingerprint,
        provenance_manifest=provenance_manifest,
        provider_contract=_public_provider_contract(validated, credentials),
    )
    _secret_free_json(
        result,
        forbidden_values=(
            credentials.api_key,
            credentials.base_url,
            _normalize_endpoint_url(credentials.base_url),
        ),
    )
    return result


def _provenance_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    return (
        before.get("source_manifest_sha256")
        == after.get("source_manifest_sha256")
        and before.get("files") == after.get("files")
        and before.get("environment") == after.get("environment")
    )


def _require_unused_output(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError("refusing to overwrite an existing artifact")


def _require_distinct_artifact_paths(output: Path, ledger: Path) -> None:
    if output.resolve(strict=False) == ledger.resolve(strict=False):
        raise DevelopmentPilotError("output and attempt ledger must be distinct paths")


def _write_new_json(
    path: Path,
    result: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> None:
    encoded = _secret_free_json(result, forbidden_values=forbidden_values)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open("x", encoding="utf-8") as handle:
            created = True
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Safety limitations:\n"
            f"  - {NO_RESUME_NOTICE}.\n"
            "  - Development-only: stop for discussion after frozen analysis."
        ),
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--env-prefix", default="DEEPSEEK")
    parser.add_argument("--config", type=Path, default=DEVELOPMENT_PILOT_CONFIG_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="authorize exactly one fresh 1,120-call development-pilot attempt",
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
        parser.error("checkpoint/resume is not implemented safely; start a fresh run")
    if not args.execute:
        parser.error("refusing external API use without --execute")

    ledger: AttemptLedger | None = None
    output_created = False
    try:
        _require_distinct_artifact_paths(args.output, args.attempt_ledger)
        _require_unused_output(args.output)
        _require_unused_output(args.attempt_ledger)
        credentials = load_provider_credentials(
            prefix=args.env_prefix,
            env_file=args.env_file,
        )
        validated = preflight_development_pilot(credentials, config=args.config)
        provider_contract = _public_provider_contract(validated, credentials)
        provenance = source_manifest(PROJECT_ROOT)
        ledger = AttemptLedger(
            args.attempt_ledger,
            provenance=provenance,
            config_sha256=_config_sha256(validated),
            provider_contract=provider_contract,
        )
        print(
            "[development-pilot] preflight ok calls=1120 worlds=8 arms=7 "
            f"model={DEVELOPMENT_PILOT_MODEL} "
            f"provider={validated['model']['provider']}",
            file=sys.stderr,
            flush=True,
        )
        print(f"[development-pilot] limitation: {NO_RESUME_NOTICE}", file=sys.stderr)
        summary = run_development_pilot(
            credentials,
            config=validated,
            provenance_manifest=provenance,
            progress_stream=sys.stderr,
            attempt_ledger=ledger,
        )
        postflight_provenance = source_manifest(PROJECT_ROOT)
        if not _provenance_unchanged(provenance, postflight_provenance):
            raise DevelopmentPilotError(
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
        output_created = True
        ledger.append(
            {
                "event": "attempt_completed",
                "at_utc": _utc_now(),
                "successful_logical_calls": DEVELOPMENT_PILOT_EXPECTED_CALLS,
                "source_manifest_sha256": provenance["source_manifest_sha256"],
                "result_artifact_written": True,
            }
        )
        ledger.close()
        output_created = False
    except BaseException as exc:
        if output_created:
            try:
                args.output.unlink(missing_ok=True)
            except OSError:
                pass
        if ledger is not None and not ledger.closed:
            try:
                ledger.append(
                    {
                        "event": "attempt_aborted",
                        "at_utc": _utc_now(),
                        "provider_failure_category": _failure_event(exc)[
                            "provider_failure_category"
                        ],
                        "result_artifact_written": False,
                    }
                )
            except BaseException:
                pass
            finally:
                ledger.close()
        print(
            "[development-pilot] aborted safely; no result artifact was written",
            file=sys.stderr,
            flush=True,
        )
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 1

    print(
        "[development-pilot] complete; 1,120-response result written exclusively",
        file=sys.stderr,
        flush=True,
    )
    return 0


__all__ = [
    "AttemptLedger",
    "DEVELOPMENT_PILOT_ARMS",
    "DEVELOPMENT_PILOT_CALLS_PER_RUN",
    "DEVELOPMENT_PILOT_CONFIG_PATH",
    "DEVELOPMENT_PILOT_EPISODE",
    "DEVELOPMENT_PILOT_EXPECTED_CALLS",
    "DEVELOPMENT_PILOT_EXPECTED_RUNS",
    "DEVELOPMENT_PILOT_MODE",
    "DEVELOPMENT_PILOT_MODEL",
    "DEVELOPMENT_PILOT_MODEL_CONFIG",
    "DEVELOPMENT_PILOT_OFFICIAL_PROVIDER",
    "DEVELOPMENT_PILOT_VOLCENGINE_CONFIG_PATH",
    "DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT",
    "DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER",
    "DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL",
    "DEVELOPMENT_PILOT_WORLDS",
    "DevelopmentPilotError",
    "NO_RESUME_NOTICE",
    "ProgressReportingGeneratorFactory",
    "build_live_generator_factory",
    "main",
    "preflight_development_pilot",
    "run_development_pilot",
    "validate_development_pilot_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
