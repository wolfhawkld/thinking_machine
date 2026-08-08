"""Guarded eight-request canary for the live candidate-format contract.

This operational canary reuses retired development world ``seed=1000`` and
runs only the two-round MTX path needed to exercise both a first-round prompt
and second-round prompts containing an archive.  It never evaluates the
private test split and can never be scientific evidence.

There is deliberately no checkpoint, retry, or resume path.  Candidate-content
schema failures consume their paid slots and are reported as canary failures;
transport, outer-envelope, and usage failures abort the attempt without a
result artifact.
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
from typing import Any, TextIO
import uuid

from .credentials import ProviderCredentials, load_provider_credentials
from .experiment import load_config, validate_config
from .policies import MultiTemperatureExchangePolicy
from .providers import (
    HTTPStatusError,
    OpenAICompatibleError,
    OpenAICompatibleGenerator,
    ResponsePayloadError,
    TransportError,
    UsagePayloadError,
)
from .provenance import PROJECT_ROOT, source_manifest
from .runner import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    EpisodeResult,
    GenerationResponse,
    run_episode,
)
from .verifier import Verifier
from .world_generator import generate_world


FORMAT_CANARY_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "format-canary.json"
)
FORMAT_CANARY_VOLCENGINE_CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "format-canary-volcengine.json"
)
FORMAT_CANARY_MODEL = "deepseek-v4-flash"
FORMAT_CANARY_OFFICIAL_PROVIDER = "deepseek-openai-compatible"
FORMAT_CANARY_VOLCENGINE_PROVIDER = "volcengine-agent-plan-openai-compatible"
FORMAT_CANARY_VOLCENGINE_RESPONSE_MODEL = "deepseek-v4-flash-ga-260731"
FORMAT_CANARY_VOLCENGINE_ENDPOINT = (
    "https://ark.cn-beijing.volces.com/api/plan/v3"
)
FORMAT_CANARY_WORLD = {"seed": 1000, "depth": 3}
FORMAT_CANARY_TEMPERATURES = (0.2, 0.7, 0.7, 1.2)
FORMAT_CANARY_EPISODE = {
    "rounds": 2,
    "candidates_per_round": 4,
    "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
    "archive_size": 4,
    "max_counterexamples_per_round": 2,
}
FORMAT_CANARY_ARM = {
    "MTX": {"kind": "multi", "temperatures": list(FORMAT_CANARY_TEMPERATURES)}
}
FORMAT_CANARY_MODEL_CONFIG = {
    "provider": FORMAT_CANARY_OFFICIAL_PROVIDER,
    "name": FORMAT_CANARY_MODEL,
    "snapshot": None,
    "structured_output": True,
}
FORMAT_CANARY_EXPECTED_CALLS = 8

_COMMON_AND_CONTENT_REQUIRED_CRITERIA = (
    "eight_completed_logical_calls",
    "one_provider_request_per_slot",
    "configured_model_exact_for_all_calls",
    "finish_reason_stop_for_all_calls",
    "usage_complete_for_all_calls",
    "request_and_response_output_caps_exact",
    "reasoning_disabled_for_all_calls",
    "provider_seed_unsupported_for_all_calls",
    "candidate_format_metadata_consistent",
    "round_candidate_slot_set_exact",
    "mtx_temperature_schedule_exact",
    "round_1_four_of_four_json_expression_and_valid",
    "round_2_archive_four_of_four_json_expression_and_valid",
    "private_test_not_evaluated",
)
_PROVIDER_CONTRACT_REQUIRED_CRITERIA = (
    "prompt_cache_contract_satisfied_for_all_calls",
    "system_fingerprint_contract_satisfied_for_all_calls",
)
_DIAGNOSTIC_TELEMETRY_FACT_CRITERIA = (
    "cache_accounting_complete_for_all_calls",
    "provider_fingerprint_present_and_stable",
)

# These two compatibility contracts were observed and frozen before any paid
# eight-call result for the Volcengine profile.  Unavailable telemetry after
# adapter normalization is a capability fact, not permission to synthesize it.
_PROVIDER_CONTRACTS: dict[str, dict[str, str]] = {
    FORMAT_CANARY_OFFICIAL_PROVIDER: {
        "request_model": FORMAT_CANARY_MODEL,
        "expected_response_model": FORMAT_CANARY_MODEL,
        "prompt_cache_capability": "hit_and_miss_token_counts_reported",
        "prompt_cache_requirement": "complete_accounting_for_every_call",
        "fingerprint_capability": "system_fingerprint_reported",
        "fingerprint_requirement": "nonempty_and_stable_for_every_call",
    },
    FORMAT_CANARY_VOLCENGINE_PROVIDER: {
        "request_model": FORMAT_CANARY_MODEL,
        "expected_response_model": FORMAT_CANARY_VOLCENGINE_RESPONSE_MODEL,
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
        "endpoint_url": FORMAT_CANARY_VOLCENGINE_ENDPOINT,
    },
}

NO_RESUME_NOTICE = (
    "checkpoint/resume is unsupported; an interrupted attempt remains only in "
    "its durable ledger and a new invocation starts all eight calls again"
)

_ARCHIVE_PROMPT_HEADER = "Previously explored candidates"
_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "candidate_expression",
        "content",
        "expression",
        "prompt",
        "provider_fingerprint",
        "raw",
        "raw_content",
        "raw_prompt",
        "raw_response",
    }
)
_EXPECTED_CONFIG = {
    "schema_version": 1,
    "status": "development-only",
    "experiment": "live-format-contract-canary",
    "worlds": [FORMAT_CANARY_WORLD],
    "episode": FORMAT_CANARY_EPISODE,
    "arms": FORMAT_CANARY_ARM,
    "model": FORMAT_CANARY_MODEL_CONFIG,
}


class FormatCanaryError(RuntimeError):
    """Raised when the canary cannot preserve its audited contract."""


def _expected_config(provider: str) -> dict[str, Any]:
    return {
        **_EXPECTED_CONFIG,
        "model": {**FORMAT_CANARY_MODEL_CONFIG, "provider": provider},
    }


def _provider_contract(config: Mapping[str, Any]) -> dict[str, str]:
    model = config.get("model")
    provider = model.get("provider") if isinstance(model, Mapping) else None
    if provider not in _PROVIDER_CONTRACTS:
        raise FormatCanaryError(
            "format-canary model.provider must select an audited provider profile"
        )
    return dict(_PROVIDER_CONTRACTS[provider])


def _normalize_endpoint_url(value: str) -> str:
    """Normalize only equivalent trailing slashes for endpoint binding."""

    return value.rstrip("/")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _config_sha256(config: Mapping[str, Any]) -> str:
    return _sha256_text(_canonical_json(config))


def _reject_sensitive_fields(value: Any) -> None:
    """Reject structures that could serialize raw request/response material."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_FIELD_NAMES:
                raise FormatCanaryError(
                    "refusing to persist raw prompt, content, expression, or credential fields"
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
        raise FormatCanaryError("canary audit data is not finite JSON") from exc
    if any(secret and secret in encoded for secret in forbidden_values):
        raise FormatCanaryError("refusing to persist a result containing a credential")
    return encoded + "\n"


def _safe_fingerprint(value: Any) -> tuple[bool, str | None]:
    if not isinstance(value, str) or not value.strip():
        return False, None
    return True, _sha256_text(value)


def validate_format_canary_config(
    config: Mapping[str, Any] | str | Path = FORMAT_CANARY_CONFIG_PATH,
) -> dict[str, Any]:
    """Validate both the generic schema and the exact eight-call contract."""

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
        raise FormatCanaryError(
            "format-canary config must exactly match the audited eight-call "
            f"contract; drifted top-level fields: {fields}"
        )
    if validated["model"]["name"] != contract["request_model"]:
        raise FormatCanaryError(
            "format-canary request model does not match its provider profile"
        )
    planned = (
        len(validated["worlds"])
        * len(validated["arms"])
        * int(validated["episode"]["rounds"])
        * int(validated["episode"]["candidates_per_round"])
    )
    if planned != FORMAT_CANARY_EXPECTED_CALLS:
        raise FormatCanaryError("format-canary call budget must be exactly eight")
    return validated


def preflight_format_canary(
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = FORMAT_CANARY_CONFIG_PATH,
) -> dict[str, Any]:
    """Reject credential, model, or protocol drift before live setup."""

    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    validated = validate_format_canary_config(config)
    contract = _provider_contract(validated)
    if credentials.model != contract["request_model"]:
        raise FormatCanaryError(
            "credential model does not match the audited format-canary model"
        )
    if (
        validated["model"]["provider"] == FORMAT_CANARY_VOLCENGINE_PROVIDER
        and _normalize_endpoint_url(credentials.base_url)
        != contract["endpoint_url"]
    ):
        raise FormatCanaryError(
            "credential endpoint does not match the audited Volcengine endpoint"
        )
    if not credentials.api_key:
        raise FormatCanaryError("format-canary API credential is empty")
    return validated


class FormatCanaryAttemptLedger:
    """Durable append-only audit events for one non-resumable attempt."""

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
                "expected_logical_calls": FORMAT_CANARY_EXPECTED_CALLS,
                "config_sha256": config_sha256,
                "source_manifest_sha256": provenance["source_manifest_sha256"],
                "model": FORMAT_CANARY_MODEL,
                "thinking": "disabled",
                "seed_supported": False,
                "retry_supported": False,
                "resume_supported": False,
                "private_test_evaluated": False,
                "evidence": False,
            }
        )

    def append(self, event: Mapping[str, Any]) -> None:
        payload = {"schema_version": 1, "attempt_id": self.attempt_id, **dict(event)}
        encoded = _secret_free_json(payload, indent=None)
        self._handle.write(encoded)
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> "FormatCanaryAttemptLedger":
        return self

    def __exit__(self, *args: Any) -> None:
        del args
        self.close()


def _failure_category(exc: BaseException) -> tuple[str | None, int | None]:
    if isinstance(exc, HTTPStatusError):
        status = exc.status_code
        safe_status = status if type(status) is int and 100 <= status <= 599 else None
        return "http_status_error", safe_status
    if isinstance(exc, UsagePayloadError):
        return "usage_payload_error", None
    if isinstance(exc, ResponsePayloadError):
        return "response_payload_error", None
    if isinstance(exc, TransportError):
        return "transport_error", None
    if isinstance(exc, OpenAICompatibleError):
        return "provider_adapter_error", None
    return None, None


class _CanaryReporter:
    """Eight-slot reporter with no assumptions about experiment arm ordering."""

    __slots__ = (
        "audits",
        "expected",
        "expected_model",
        "ledger",
        "started",
        "stream",
        "succeeded",
        "_starts",
    )

    def __init__(
        self,
        *,
        expected: int,
        expected_model: str,
        stream: TextIO,
        ledger: FormatCanaryAttemptLedger | None,
    ) -> None:
        self.expected = expected
        self.expected_model = expected_model
        self.stream = stream
        self.ledger = ledger
        self.started = 0
        self.succeeded = 0
        self.audits: list[dict[str, Any]] = []
        self._starts: dict[int, dict[str, Any]] = {}

    def start(self, prompt: str, kwargs: Mapping[str, Any]) -> int:
        if self.started >= self.expected:
            raise FormatCanaryError("provider started more requests than budgeted")
        self.started += 1
        serial_index = self.started
        start = {
            "serial_index": serial_index,
            "round_index": kwargs.get("round_index"),
            "candidate_index": kwargs.get("candidate_index"),
            "temperature": kwargs.get("temperature"),
            "max_output_tokens": kwargs.get("max_output_tokens"),
            "prompt_sha256": _sha256_text(prompt),
            "archive_context_present": _ARCHIVE_PROMPT_HEADER in prompt,
        }
        self._starts[serial_index] = start
        if self.ledger is not None:
            self.ledger.append(
                {
                    "event": "logical_request_started",
                    "at_utc": _utc_now(),
                    **start,
                }
            )
        return serial_index

    def record(self, serial_index: int, response: GenerationResponse) -> None:
        if self.succeeded >= self.expected or serial_index not in self._starts:
            raise FormatCanaryError("provider returned more responses than budgeted")
        fingerprint_present, fingerprint_sha256 = _safe_fingerprint(
            getattr(response, "provider_fingerprint", None)
        )
        cache_hit = response.prompt_cache_hit_tokens
        cache_miss = response.prompt_cache_miss_tokens
        cache_accounting_complete = (
            type(cache_hit) is int
            and type(cache_miss) is int
            and response.input_tokens == cache_hit + cache_miss
        )
        audit = {
            **self._starts[serial_index],
            "provider_model_exact": response.provider_model == self.expected_model,
            "finish_reason_stop": response.finish_reason == "stop",
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "provider_request_count": response.provider_request_count,
            "seed_supported": response.seed_supported,
            "prompt_cache_hit_tokens": cache_hit,
            "prompt_cache_miss_tokens": cache_miss,
            "cache_accounting_complete": cache_accounting_complete,
            "reasoning_tokens": response.reasoning_tokens,
            "candidate_format": response.candidate_format,
            "provider_fingerprint_unavailable": (
                getattr(response, "provider_fingerprint", None) is None
            ),
            "provider_fingerprint_present": fingerprint_present,
            "provider_fingerprint_sha256": fingerprint_sha256,
        }
        if self.ledger is not None:
            self.ledger.append(
                {
                    "event": "logical_request_succeeded",
                    "at_utc": _utc_now(),
                    **audit,
                }
            )
        self.audits.append(audit)
        self.succeeded += 1
        label = response.candidate_format or "unclassified"
        self.stream.write(
            "[format-canary] response "
            f"{self.succeeded:02d}/{self.expected:02d} ok "
            f"candidate_format={label} input_tokens={response.input_tokens} "
            f"output_tokens={response.output_tokens}\n"
        )
        self.stream.flush()

    def fail(self, serial_index: int, exc: BaseException) -> None:
        if self.ledger is None:
            return
        category, status = _failure_category(exc)
        event: dict[str, Any] = {
            "event": "logical_request_failed_or_ambiguous",
            "at_utc": _utc_now(),
            "serial_index": serial_index,
            "error_type": type(exc).__name__,
        }
        if category is not None:
            event["provider_failure_category"] = category
        if status is not None:
            event["http_status_code"] = status
        self.ledger.append(event)


class _ReportingGenerator:
    __slots__ = ("_delegate", "_reporter")

    def __init__(self, delegate: Any, reporter: _CanaryReporter) -> None:
        self._delegate = delegate
        self._reporter = reporter

    def generate(self, prompt: str, **kwargs: Any) -> GenerationResponse:
        serial_index = self._reporter.start(prompt, kwargs)
        try:
            method = getattr(self._delegate, "generate", None)
            if not callable(method):
                raise TypeError("live generator must expose generate()")
            response = method(prompt, **kwargs)
        except BaseException as exc:
            self._reporter.fail(serial_index, exc)
            raise
        if not isinstance(response, GenerationResponse):
            error = FormatCanaryError(
                "live generator must return a metered GenerationResponse"
            )
            self._reporter.fail(serial_index, error)
            raise error
        self._reporter.record(serial_index, response)
        return response


def build_live_generator(credentials: ProviderCredentials) -> OpenAICompatibleGenerator:
    """Build the thinking-disabled, seed-free, one-attempt provider adapter."""

    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    return OpenAICompatibleGenerator(
        base_url=credentials.base_url,
        api_key=credentials.api_key,
        model=FORMAT_CANARY_MODEL,
        seed_supported=False,
        timeout=60.0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _call_usage_complete(call: Mapping[str, Any]) -> bool:
    return (
        type(call.get("input_tokens")) is int
        and call["input_tokens"] >= 0
        and type(call.get("output_tokens")) is int
        and call["output_tokens"] >= 0
        and isinstance(call.get("latency_ms"), (int, float))
        and not isinstance(call.get("latency_ms"), bool)
        and call["latency_ms"] >= 0
    )


def _round_summary(
    round_index: int,
    calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    expected_archive = round_index == 1
    format_valid_count = sum(
        call.get("candidate_format") == "json_expression"
        and call.get("syntax_valid") is True
        and call.get("runtime_valid") is True
        for call in calls
    )
    archive_present_all = bool(calls) and all(
        call.get("archive_context_present") is True for call in calls
    )
    archive_absent_all = bool(calls) and all(
        call.get("archive_context_present") is False for call in calls
    )
    archive_context_exact = (
        archive_present_all if expected_archive else archive_absent_all
    )
    return {
        "round_index": round_index,
        "round_label": f"R{round_index + 1}",
        "logical_calls": len(calls),
        "archive_context_expected": expected_archive,
        "archive_context_present_for_all_calls": archive_present_all,
        "archive_context_absent_for_all_calls": archive_absent_all,
        "archive_context_exact": archive_context_exact,
        "json_expression_syntax_runtime_valid_calls": format_valid_count,
        "passed": (
            len(calls) == 4
            and format_valid_count == 4
            and archive_context_exact
        ),
        "calls": [dict(call) for call in calls],
    }


def _build_artifact(
    *,
    credentials: ProviderCredentials,
    config: Mapping[str, Any],
    world: Any,
    result: EpisodeResult,
    reporter: _CanaryReporter,
    provenance_manifest: Mapping[str, Any] | None,
    attempt_id: str | None,
) -> dict[str, Any]:
    contract = _provider_contract(config)
    provider_profile = str(config["model"]["provider"])
    records = [record for round_records in result.rounds for record in round_records]
    if len(records) != len(reporter.audits):
        raise FormatCanaryError("response audit and candidate slot counts diverged")

    calls: list[dict[str, Any]] = []
    for audit, record in zip(reporter.audits, records, strict=True):
        calls.append(
            {
                **audit,
                "syntax_valid": bool(record.syntax_valid),
                "runtime_valid": bool(record.runtime_valid),
                "failure_codes": list(record.failure_codes),
                "candidate_format_metadata_consistent": (
                    record.candidate_format == audit["candidate_format"]
                ),
            }
        )

    expected_schedule = [list(FORMAT_CANARY_TEMPERATURES)] * 2
    observed_schedule = [list(row) for row in result.slot_temperatures]
    fingerprints = {
        call["provider_fingerprint_sha256"]
        for call in calls
        if call["provider_fingerprint_sha256"] is not None
    }
    round_summaries = [
        _round_summary(
            round_index,
            [call for call in calls if call["round_index"] == round_index],
        )
        for round_index in range(2)
    ]
    expected_slots = {
        (round_index, candidate_index)
        for round_index in range(2)
        for candidate_index in range(4)
    }
    observed_slots = {
        (call["round_index"], call["candidate_index"])
        for call in calls
    }
    if provider_profile == FORMAT_CANARY_OFFICIAL_PROVIDER:
        cache_contract_satisfied = all(
            call["cache_accounting_complete"] is True for call in calls
        )
        fingerprint_contract_satisfied = (
            len(calls) == FORMAT_CANARY_EXPECTED_CALLS
            and all(call["provider_fingerprint_present"] is True for call in calls)
            and len(fingerprints) == 1
        )
    else:
        cache_contract_satisfied = all(
            call["prompt_cache_hit_tokens"] is None
            and call["prompt_cache_miss_tokens"] is None
            for call in calls
        )
        fingerprint_contract_satisfied = (
            len(calls) == FORMAT_CANARY_EXPECTED_CALLS
            and all(
                call["provider_fingerprint_unavailable"] is True for call in calls
            )
            and not fingerprints
        )
    criteria = {
        "eight_completed_logical_calls": (
            reporter.started == FORMAT_CANARY_EXPECTED_CALLS
            and reporter.succeeded == FORMAT_CANARY_EXPECTED_CALLS
            and len(calls) == FORMAT_CANARY_EXPECTED_CALLS
        ),
        "one_provider_request_per_slot": all(
            call["provider_request_count"] == 1 for call in calls
        ),
        "configured_model_exact_for_all_calls": all(
            call["provider_model_exact"] is True for call in calls
        ),
        "finish_reason_stop_for_all_calls": all(
            call["finish_reason_stop"] is True for call in calls
        ),
        "usage_complete_for_all_calls": all(_call_usage_complete(call) for call in calls),
        "request_and_response_output_caps_exact": all(
            call["max_output_tokens"] == FORMAT_CANARY_EPISODE["max_output_tokens"]
            and call["output_tokens"] <= FORMAT_CANARY_EPISODE["max_output_tokens"]
            for call in calls
        ),
        # These two legacy fields remain literal telemetry facts.  They are
        # diagnostics for profiles that do not expose the corresponding data.
        "cache_accounting_complete_for_all_calls": all(
            call["cache_accounting_complete"] is True for call in calls
        ),
        "prompt_cache_contract_satisfied_for_all_calls": cache_contract_satisfied,
        "reasoning_disabled_for_all_calls": all(
            call["reasoning_tokens"] in {None, 0} for call in calls
        ),
        "provider_seed_unsupported_for_all_calls": all(
            call["seed_supported"] is False for call in calls
        ),
        "provider_fingerprint_present_and_stable": (
            len(calls) == FORMAT_CANARY_EXPECTED_CALLS
            and all(call["provider_fingerprint_present"] is True for call in calls)
            and len(fingerprints) == 1
        ),
        "system_fingerprint_contract_satisfied_for_all_calls": (
            fingerprint_contract_satisfied
        ),
        "candidate_format_metadata_consistent": all(
            call["candidate_format_metadata_consistent"] is True for call in calls
        ),
        "round_candidate_slot_set_exact": observed_slots == expected_slots,
        "mtx_temperature_schedule_exact": observed_schedule == expected_schedule,
        "round_1_four_of_four_json_expression_and_valid": round_summaries[0]["passed"],
        "round_2_archive_four_of_four_json_expression_and_valid": round_summaries[1][
            "passed"
        ],
        "private_test_not_evaluated": result.final_test is None,
    }
    required_criterion_names = (
        *_COMMON_AND_CONTENT_REQUIRED_CRITERIA,
        *_PROVIDER_CONTRACT_REQUIRED_CRITERIA,
    )
    passed = all(criteria[name] is True for name in required_criterion_names)
    provider_requests = sum(int(call["provider_request_count"]) for call in calls)
    provider_metadata: dict[str, Any] = {
        "provider": provider_profile,
        "request_model": credentials.model,
        "expected_model": contract["expected_response_model"],
        "expected_response_model": contract["expected_response_model"],
        "credential_present": True,
        "endpoint_mode": "chat-completions",
        "capability_contract": {
            "scope": "provider_telemetry_only_not_candidate_content",
            "prompt_cache_usage": {
                "observed_capability": contract["prompt_cache_capability"],
                "pass_requirement": contract["prompt_cache_requirement"],
            },
            "system_fingerprint": {
                "observed_capability": contract["fingerprint_capability"],
                "pass_requirement": contract["fingerprint_requirement"],
            },
        },
        "fingerprint_sha256_values": sorted(fingerprints),
        "fingerprint_value_count": len(fingerprints),
    }
    if provider_profile == FORMAT_CANARY_VOLCENGINE_PROVIDER:
        normalized_endpoint = _normalize_endpoint_url(credentials.base_url)
        provider_metadata["endpoint_contract"] = {
            "normalization": "remove_trailing_slashes_only",
            "normalized_url_sha256": _sha256_text(normalized_endpoint),
            "contract_satisfied": normalized_endpoint == contract["endpoint_url"],
        }
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "kind": "live-format-contract-canary",
        "completed_at_utc": _utc_now(),
        "attempt_id": attempt_id,
        "evidence": False,
        "evidence_scope": "non-evidence",
        "evidence_reason": (
            "eight-call operational format check on a retired development world; "
            "private test was not evaluated"
        ),
        "passed": passed,
        "outcome": "passed" if passed else "failed",
        "config_sha256": _config_sha256(config),
        "protocol": {
            "world_seed": FORMAT_CANARY_WORLD["seed"],
            "world_depth": FORMAT_CANARY_WORLD["depth"],
            "world_hash": str(world.world_hash),
            "arm_id": "MTX",
            "rounds": 2,
            "candidates_per_round": 4,
            "archive_size": 4,
            "max_counterexamples_per_round": 2,
            "counterexamples_released": len(result.counterexamples),
            "max_output_tokens": FORMAT_CANARY_EPISODE["max_output_tokens"],
            "slot_temperature_trajectory": observed_schedule,
            "thinking": "disabled",
            "provider_seed_supported": False,
            "provider_seed_sent": False,
            "retry_supported": False,
            "resume_supported": False,
            "private_test_evaluated": False,
            "candidate_content_gate": {
                "scope": "generated_candidate_content",
                "requirement": (
                    "json_expression_with_dsl_syntax_and_runtime_valid_for_all_8_calls"
                ),
            },
        },
        "provider": provider_metadata,
        "budget": {
            "logical_calls_expected": FORMAT_CANARY_EXPECTED_CALLS,
            "logical_calls_started": reporter.started,
            "logical_calls_succeeded": reporter.succeeded,
            "provider_requests": provider_requests,
            "retry_count": provider_requests - len(calls),
            "actual_input_tokens": sum(int(call["input_tokens"]) for call in calls),
            "actual_output_tokens": sum(int(call["output_tokens"]) for call in calls),
            "prompt_cache_hit_tokens": sum(
                int(call["prompt_cache_hit_tokens"])
                for call in calls
                if call["prompt_cache_hit_tokens"] is not None
            ),
            "prompt_cache_miss_tokens": sum(
                int(call["prompt_cache_miss_tokens"])
                for call in calls
                if call["prompt_cache_miss_tokens"] is not None
            ),
        },
        "criteria": criteria,
        "criteria_contract": {
            "aggregation": "all_required_criteria_true",
            "required_criterion_names": list(required_criterion_names),
            "common_and_content_required_criterion_names": list(
                _COMMON_AND_CONTENT_REQUIRED_CRITERIA
            ),
            "provider_contract_required_criterion_names": list(
                _PROVIDER_CONTRACT_REQUIRED_CRITERIA
            ),
            "diagnostic_telemetry_fact_criterion_names": list(
                _DIAGNOSTIC_TELEMETRY_FACT_CRITERIA
            ),
        },
        "rounds": round_summaries,
    }
    if provenance_manifest is not None:
        artifact["provenance"] = json.loads(
            json.dumps(provenance_manifest, ensure_ascii=False, allow_nan=False)
        )
    _secret_free_json(artifact, forbidden_values=(credentials.api_key,))
    return artifact


def run_format_canary(
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = FORMAT_CANARY_CONFIG_PATH,
    generator: Any | None = None,
    progress_stream: TextIO | None = None,
    attempt_ledger: FormatCanaryAttemptLedger | None = None,
    provenance_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute the exact eight slots and return a sanitized non-evidence artifact."""

    validated = preflight_format_canary(credentials, config=config)
    contract = _provider_contract(validated)
    world = generate_world(
        FORMAT_CANARY_WORLD["seed"],
        depth=FORMAT_CANARY_WORLD["depth"],
    )
    delegate = build_live_generator(credentials) if generator is None else generator
    reporter = _CanaryReporter(
        expected=FORMAT_CANARY_EXPECTED_CALLS,
        expected_model=contract["expected_response_model"],
        stream=sys.stderr if progress_stream is None else progress_stream,
        ledger=attempt_ledger,
    )
    verifier = Verifier(
        world,
        counterexample_limit=FORMAT_CANARY_EPISODE["max_counterexamples_per_round"],
    )
    result = run_episode(
        world,
        _ReportingGenerator(delegate, reporter),
        verifier=verifier,
        policy=MultiTemperatureExchangePolicy(FORMAT_CANARY_TEMPERATURES),
        rounds=FORMAT_CANARY_EPISODE["rounds"],
        candidates_per_round=FORMAT_CANARY_EPISODE["candidates_per_round"],
        archive_capacity=FORMAT_CANARY_EPISODE["archive_size"],
        max_counterexamples=(
            FORMAT_CANARY_EPISODE["rounds"]
            * FORMAT_CANARY_EPISODE["max_counterexamples_per_round"]
        ),
        max_output_tokens=FORMAT_CANARY_EPISODE["max_output_tokens"],
        max_counterexamples_per_round=FORMAT_CANARY_EPISODE[
            "max_counterexamples_per_round"
        ],
        # Provider seeding is unsupported, so no seed enters the adapter call.
        seed=None,
        evaluate_test=False,
    )
    return _build_artifact(
        credentials=credentials,
        config=validated,
        world=world,
        result=result,
        reporter=reporter,
        provenance_manifest=provenance_manifest,
        attempt_id=None if attempt_ledger is None else attempt_ledger.attempt_id,
    )


def _write_new_json(
    path: Path,
    result: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> None:
    """Durably write a sanitized artifact using exclusive creation."""

    encoded = _secret_free_json(result, forbidden_values=forbidden_values)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _require_unused_path(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing path: {path}")


def _require_distinct_paths(left: Path, right: Path) -> None:
    if left.resolve(strict=False) == right.resolve(strict=False):
        raise FormatCanaryError("output and attempt-ledger paths must be distinct")


def _source_manifest_unchanged(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    try:
        return (
            before["source_manifest_sha256"] == after["source_manifest_sha256"]
            and before["environment"].get("git_head")
            == after["environment"].get("git_head")
        )
    except (KeyError, TypeError, AttributeError):
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Safety limitation: {NO_RESUME_NOTICE}.",
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--env-prefix", default="DEEPSEEK")
    parser.add_argument("--config", type=Path, default=FORMAT_CANARY_CONFIG_PATH)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempt-ledger", type=Path, required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="authorize one fresh eight-request live format canary",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="unsupported; partial canary attempts cannot be resumed",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.resume:
        parser.error("checkpoint/resume is not implemented; start one fresh attempt")
    if not args.execute:
        parser.error("refusing external API use without --execute")

    ledger: FormatCanaryAttemptLedger | None = None
    artifact_written = False
    try:
        _require_distinct_paths(args.output, args.attempt_ledger)
        _require_unused_path(args.output)
        _require_unused_path(args.attempt_ledger)
        credentials = load_provider_credentials(
            prefix=args.env_prefix,
            env_file=args.env_file,
        )
        validated = preflight_format_canary(credentials, config=args.config)
        provenance = source_manifest(PROJECT_ROOT)
        ledger = FormatCanaryAttemptLedger(
            args.attempt_ledger,
            provenance=provenance,
            config_sha256=_config_sha256(validated),
        )
        print(
            "[format-canary] preflight ok calls=8 rounds=2 arm=MTX "
            f"model={FORMAT_CANARY_MODEL}",
            file=sys.stderr,
            flush=True,
        )
        print(f"[format-canary] limitation: {NO_RESUME_NOTICE}", file=sys.stderr)
        artifact = run_format_canary(
            credentials,
            config=validated,
            progress_stream=sys.stderr,
            attempt_ledger=ledger,
            provenance_manifest=provenance,
        )
        postflight_provenance = source_manifest(PROJECT_ROOT)
        if not _source_manifest_unchanged(provenance, postflight_provenance):
            raise FormatCanaryError(
                "protocol source tree changed during the paid canary attempt"
            )
        _write_new_json(
            args.output,
            artifact,
            forbidden_values=(credentials.api_key,),
        )
        artifact_written = True
        ledger.append(
            {
                "event": "attempt_completed",
                "at_utc": _utc_now(),
                "successful_logical_calls": FORMAT_CANARY_EXPECTED_CALLS,
                "source_manifest_sha256": provenance["source_manifest_sha256"],
                "result_artifact_written": True,
                "passed": artifact["passed"],
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
                        "result_artifact_written": artifact_written,
                    }
                )
            finally:
                ledger.close()
        print(
            "[format-canary] aborted safely; no new result was accepted "
            f"(error_type={type(exc).__name__})",
            file=sys.stderr,
            flush=True,
        )
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return 1

    print(
        "[format-canary] complete; result written exclusively "
        f"passed={str(bool(artifact['passed'])).lower()}",
        file=sys.stderr,
        flush=True,
    )
    return 0 if artifact["passed"] is True else 1


__all__ = [
    "FORMAT_CANARY_ARM",
    "FORMAT_CANARY_CONFIG_PATH",
    "FORMAT_CANARY_EPISODE",
    "FORMAT_CANARY_EXPECTED_CALLS",
    "FORMAT_CANARY_MODEL",
    "FORMAT_CANARY_MODEL_CONFIG",
    "FORMAT_CANARY_OFFICIAL_PROVIDER",
    "FORMAT_CANARY_TEMPERATURES",
    "FORMAT_CANARY_VOLCENGINE_CONFIG_PATH",
    "FORMAT_CANARY_VOLCENGINE_ENDPOINT",
    "FORMAT_CANARY_VOLCENGINE_PROVIDER",
    "FORMAT_CANARY_VOLCENGINE_RESPONSE_MODEL",
    "FORMAT_CANARY_WORLD",
    "FormatCanaryAttemptLedger",
    "FormatCanaryError",
    "NO_RESUME_NOTICE",
    "build_live_generator",
    "main",
    "preflight_format_canary",
    "run_format_canary",
    "validate_format_canary_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
