"""Immutable per-slot recovery state for v3 development campaigns.

The v2 pilot commits complete 20-call shards.  V3 keeps that scientific shard
boundary, but also records enough *operational* state to resume a partially
completed shard without silently drawing a second candidate for a completed
logical slot.  No prompt, request body, endpoint, credential, or raw provider
response is persisted here; those values are represented only by hashes or by
the normalized :class:`~src.runner.GenerationResponse` fields needed for an
exact runner replay.

The state machine is deliberately conservative.  A process that disappears
after publishing an attempt start but before publishing either a retryable
outcome or a call checkpoint leaves an ``unresolved`` slot.  It is never
silently retried by this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator

from .dsl import parse_sexpr, to_sexpr
from .pilot_checkpoint import (
    INVALID_CANDIDATE_SENTINEL,
    PilotCheckpointError,
    canonical_json_bytes,
    load_envelope,
    publish_envelope,
)


V3_COORDINATOR_VERSION = "logical-slot-recovery-v1"
PHYSICAL_ATTEMPT_DIRECTORY_NAME = "physical-attempts"
CALL_CHECKPOINT_DIRECTORY_NAME = "call-checkpoints"
SHARD_LOCK_DIRECTORY_NAME = "shard-locks"
MAX_PHYSICAL_ATTEMPTS = 3
SLOTS_PER_SHARD = 20
V3_TOTAL_SHARDS = 104

_ATTEMPT_FILE_PATTERN = re.compile(
    r"^shard-([0-9]{4})-slot-([0-9]{2})-attempt-([0-9]{2})-(start|outcome)\.json$"
)
_CALL_FILE_PATTERN = re.compile(r"^shard-([0-9]{4})-slot-([0-9]{2})\.json$")

_RETRYABLE_TRANSPORT_CATEGORIES = frozenset(
    {
        "timeout",
        "dns",
        "tls",
        "connection_refused",
        "connection_reset",
        "network_io",
    }
)
_FATAL_TRANSPORT_CATEGORIES = frozenset(
    {
        "injected_transport_exception",
        "local_request_configuration",
        "local_transport_contract",
    }
)
_FATAL_RESPONSE_CATEGORIES = frozenset(
    {
        "response_payload",
        "usage_payload",
        "provider_model_contract",
        "finish_reason_contract",
        "output_cap_contract",
        "cache_telemetry_contract",
        "provider_fingerprint_contract",
    }
)
_OUTCOME_CLASSES = frozenset(
    {
        "retryable_transport",
        "retryable_http",
        "fatal_transport",
        "fatal_http",
        "fatal_response_contract",
    }
)
_CANDIDATE_FORMATS = frozenset(
    {
        "json_expression",
        "invalid_json",
        "json_non_object",
        "missing_expression",
        "extra_fields",
        "non_string_expression",
        "empty_expression",
        "null_content",
        "empty_content",
        "non_string_content",
    }
)
_START_KEYS = frozenset(
    {
        "shard_index",
        "slot_index",
        "attempt_ordinal",
        "request_body_sha256",
        "prompt_sha256",
        "route_binding_sha256",
        "transaction_binding_sha256",
        "coordinator_version",
    }
)
_OUTCOME_KEYS = frozenset(
    {
        "shard_index",
        "slot_index",
        "attempt_ordinal",
        "start_payload_sha256",
        "outcome_class",
        "failure_category",
        "http_status",
        "known_input_tokens",
        "known_output_tokens",
        "known_latency_ms",
    }
)
_CALL_KEYS = frozenset(
    {
        "shard_index",
        "slot_index",
        "accepted_attempt",
        "request_body_sha256",
        "prompt_sha256",
        "route_binding_sha256",
        "transaction_binding_sha256",
        "accepted_start_payload_sha256",
        "coordinator_version",
        "response",
    }
)
_RESPONSE_KEYS = frozenset(
    {
        "candidate_expression",
        "candidate_parse_status",
        "candidate_format",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "accepted_provider_request_count",
        "seed_supported",
        "provider_model",
        "finish_reason",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
        "provider_fingerprint_sha256",
    }
)


class PilotCheckpointV3Error(PilotCheckpointError):
    """Raised when per-slot v3 recovery state is unsafe or inconsistent."""


@dataclass(frozen=True)
class SlotRecoveryState:
    """Validated recovery state for one logical candidate slot."""

    status: str
    next_attempt: int | None
    started_attempts: tuple[int, ...]
    retryable_outcomes: tuple[int, ...]
    accepted_attempt: int | None = None
    call_payload_sha256: str | None = None
    fatal_attempt: int | None = None


@dataclass(frozen=True)
class ShardRecoveryState:
    """Committed logical-slot prefix and at most one active next slot."""

    committed_prefix_count: int
    next_slot_index: int | None
    active_status: str | None
    slot_states: tuple[SlotRecoveryState, ...]


def _integer(name: str, value: Any, *, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PilotCheckpointV3Error(f"{name} must be an integer in [{low}, {high}]")
    return value


def _indices(shard_index: Any, slot_index: Any) -> tuple[int, int]:
    # V3 campaign sizes are manifest-defined, so storage only imposes a broad
    # safety bound.  The orchestrator separately verifies membership in plan.
    shard = _integer("shard_index", shard_index, low=0, high=9999)
    slot = _integer("slot_index", slot_index, low=0, high=SLOTS_PER_SHARD - 1)
    return shard, slot


def _sha256(name: str, value: Any) -> str:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise PilotCheckpointV3Error(f"{name} must be a lowercase SHA-256")
    return value


def _optional_string(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PilotCheckpointV3Error(f"{name} must be a non-empty string or null")
    return value


def _exact_keys(name: str, value: Mapping[str, Any], expected: frozenset[str]) -> None:
    actual = frozenset(str(key) for key in value)
    if actual != expected:
        raise PilotCheckpointV3Error(
            f"{name} keys drifted: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def attempt_start_path(
    campaign_dir: str | Path,
    shard_index: int,
    slot_index: int,
    attempt_ordinal: int,
) -> Path:
    shard, slot = _indices(shard_index, slot_index)
    attempt = _integer(
        "attempt_ordinal", attempt_ordinal, low=1, high=MAX_PHYSICAL_ATTEMPTS
    )
    return (
        Path(campaign_dir)
        / PHYSICAL_ATTEMPT_DIRECTORY_NAME
        / f"shard-{shard:04d}-slot-{slot:02d}-attempt-{attempt:02d}-start.json"
    )


def attempt_outcome_path(
    campaign_dir: str | Path,
    shard_index: int,
    slot_index: int,
    attempt_ordinal: int,
) -> Path:
    return attempt_start_path(
        campaign_dir, shard_index, slot_index, attempt_ordinal
    ).with_name(
        attempt_start_path(
            campaign_dir, shard_index, slot_index, attempt_ordinal
        ).name.replace("-start.json", "-outcome.json")
    )


def call_checkpoint_path(
    campaign_dir: str | Path,
    shard_index: int,
    slot_index: int,
) -> Path:
    shard, slot = _indices(shard_index, slot_index)
    return (
        Path(campaign_dir)
        / CALL_CHECKPOINT_DIRECTORY_NAME
        / f"shard-{shard:04d}-slot-{slot:02d}.json"
    )


def shard_lock_path(campaign_dir: str | Path, shard_index: int) -> Path:
    shard = _integer("shard_index", shard_index, low=0, high=9999)
    return Path(campaign_dir) / SHARD_LOCK_DIRECTORY_NAME / f"shard-{shard:04d}.lock"


@contextmanager
def acquire_shard_lock(
    campaign_dir: str | Path,
    shard_index: int,
    *,
    blocking: bool = False,
) -> Iterator[Path]:
    """Hold a process-scoped advisory lock for one shard.

    The kernel releases the lock after a crash.  The mode-0600 lock file is not
    a commit artifact and its contents are deliberately empty.
    """

    if type(blocking) is not bool:
        raise TypeError("blocking must be a boolean")
    path = shard_lock_path(campaign_dir, shard_index)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise PilotCheckpointV3Error("cannot safely open shard lock") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise PilotCheckpointV3Error("shard lock must be a regular file")
        os.fchmod(descriptor, 0o600)
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise PilotCheckpointV3Error("shard is already owned by another process") from exc
        yield path
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_start_payload(payload: Mapping[str, Any]) -> None:
    _exact_keys("attempt start", payload, _START_KEYS)
    _indices(payload.get("shard_index"), payload.get("slot_index"))
    _integer(
        "attempt_ordinal",
        payload.get("attempt_ordinal"),
        low=1,
        high=MAX_PHYSICAL_ATTEMPTS,
    )
    _sha256("request_body_sha256", payload.get("request_body_sha256"))
    _sha256("prompt_sha256", payload.get("prompt_sha256"))
    _sha256("route_binding_sha256", payload.get("route_binding_sha256"))
    _sha256(
        "transaction_binding_sha256", payload.get("transaction_binding_sha256")
    )
    if payload.get("coordinator_version") != V3_COORDINATOR_VERSION:
        raise PilotCheckpointV3Error("attempt start coordinator version drifted")


def _validate_outcome_payload(payload: Mapping[str, Any]) -> None:
    _exact_keys("attempt outcome", payload, _OUTCOME_KEYS)
    _indices(payload.get("shard_index"), payload.get("slot_index"))
    _integer(
        "attempt_ordinal",
        payload.get("attempt_ordinal"),
        low=1,
        high=MAX_PHYSICAL_ATTEMPTS,
    )
    _sha256("start_payload_sha256", payload.get("start_payload_sha256"))
    outcome_class = payload.get("outcome_class")
    if outcome_class not in _OUTCOME_CLASSES:
        raise PilotCheckpointV3Error("attempt outcome class is not in the closed set")
    category = payload.get("failure_category")
    status = payload.get("http_status")
    if status is not None:
        _integer("http_status", status, low=100, high=599)
    known_values = (
        payload.get("known_input_tokens"),
        payload.get("known_output_tokens"),
        payload.get("known_latency_ms"),
    )
    if any(value is not None for value in known_values):
        if any(value is None for value in known_values):
            raise PilotCheckpointV3Error(
                "known discarded response telemetry must be complete or absent"
            )
        for name, value in zip(
            ("known_input_tokens", "known_output_tokens"), known_values[:2]
        ):
            if type(value) is not int or value < 0:
                raise PilotCheckpointV3Error(
                    f"{name} must be a non-negative integer"
                )
        latency = known_values[2]
        if isinstance(latency, bool) or not isinstance(latency, (int, float)):
            raise PilotCheckpointV3Error(
                "known_latency_ms must be finite and non-negative"
            )
        if not math.isfinite(float(latency)) or float(latency) < 0:
            raise PilotCheckpointV3Error(
                "known_latency_ms must be finite and non-negative"
            )
    if outcome_class == "retryable_transport":
        if category not in _RETRYABLE_TRANSPORT_CATEGORIES or status is not None:
            raise PilotCheckpointV3Error("invalid retryable transport outcome")
    elif outcome_class == "retryable_http":
        if status != 429 and not (type(status) is int and 500 <= status <= 599):
            raise PilotCheckpointV3Error("retryable HTTP outcome must be 429 or 5xx")
        if category is not None:
            raise PilotCheckpointV3Error("retryable HTTP outcome cannot have a category")
    elif outcome_class == "fatal_transport":
        if category not in _FATAL_TRANSPORT_CATEGORIES or status is not None:
            raise PilotCheckpointV3Error("invalid fatal transport outcome")
    elif outcome_class == "fatal_http":
        if type(status) is not int or 200 <= status <= 299 or status == 429 or 500 <= status <= 599:
            raise PilotCheckpointV3Error("fatal HTTP outcome has retryable/success status")
        if category is not None:
            raise PilotCheckpointV3Error("fatal HTTP outcome cannot have a category")
    else:
        # The provider adapter has already established a 2xx response before
        # it parses the envelope, but its normalized exception deliberately
        # does not retain the exact status.  Null is therefore honest; a
        # numeric value, when available, must be in the 2xx class.
        if category not in _FATAL_RESPONSE_CATEGORIES or not (
            status is None or (type(status) is int and 200 <= status <= 299)
        ):
            raise PilotCheckpointV3Error("invalid fatal response-contract outcome")
        parsed_contract_categories = _FATAL_RESPONSE_CATEGORIES - {
            "response_payload",
            "usage_payload",
        }
        if (category in parsed_contract_categories) != all(
            value is not None for value in known_values
        ):
            raise PilotCheckpointV3Error(
                "fatal response-contract telemetry presence drifted"
            )
    if outcome_class != "fatal_response_contract" and any(
        value is not None for value in known_values
    ):
        raise PilotCheckpointV3Error(
            "only a parsed fatal response may persist discarded telemetry"
        )


def _validate_response_payload(response: Mapping[str, Any]) -> None:
    _exact_keys("call response", response, _RESPONSE_KEYS)
    expression = response.get("candidate_expression")
    parse_status = response.get("candidate_parse_status")
    if expression == INVALID_CANDIDATE_SENTINEL:
        if parse_status != "invalid_candidate":
            raise PilotCheckpointV3Error("invalid sentinel requires invalid_candidate status")
    else:
        if not isinstance(expression, str) or not expression:
            raise PilotCheckpointV3Error("call checkpoint lacks a candidate expression")
        try:
            canonical = to_sexpr(parse_sexpr(expression))
        except Exception as exc:
            raise PilotCheckpointV3Error("call checkpoint expression is not frozen DSL") from exc
        if expression != canonical or parse_status != "canonical_dsl":
            raise PilotCheckpointV3Error("call checkpoint expression is not canonical")
    candidate_format = response.get("candidate_format")
    if candidate_format not in _CANDIDATE_FORMATS:
        raise PilotCheckpointV3Error("candidate_format is not in the closed set")
    if parse_status == "canonical_dsl" and candidate_format != "json_expression":
        raise PilotCheckpointV3Error(
            "canonical DSL requires candidate_format=json_expression"
        )
    for name in ("input_tokens", "output_tokens"):
        value = response.get(name)
        if type(value) is not int or value < 0:
            raise PilotCheckpointV3Error(f"{name} must be a non-negative integer")
    latency = response.get("latency_ms")
    if isinstance(latency, bool) or not isinstance(latency, (int, float)):
        raise PilotCheckpointV3Error("latency_ms must be finite and non-negative")
    if not math.isfinite(float(latency)) or float(latency) < 0:
        raise PilotCheckpointV3Error("latency_ms must be finite and non-negative")
    if response.get("accepted_provider_request_count") != 1:
        raise PilotCheckpointV3Error("accepted response must represent one physical request")
    seed_supported = response.get("seed_supported")
    if seed_supported is not None and type(seed_supported) is not bool:
        raise PilotCheckpointV3Error("seed_supported must be bool or null")
    for name in (
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
    ):
        value = response.get(name)
        if value is not None and (type(value) is not int or value < 0):
            raise PilotCheckpointV3Error(f"{name} must be a non-negative integer or null")
    for name in ("provider_model", "finish_reason"):
        _optional_string(name, response.get(name))
    fingerprint_hash = response.get("provider_fingerprint_sha256")
    if fingerprint_hash is not None:
        _sha256("provider_fingerprint_sha256", fingerprint_hash)


def _validate_call_payload(payload: Mapping[str, Any]) -> None:
    _exact_keys("call checkpoint", payload, _CALL_KEYS)
    _indices(payload.get("shard_index"), payload.get("slot_index"))
    _integer(
        "accepted_attempt",
        payload.get("accepted_attempt"),
        low=1,
        high=MAX_PHYSICAL_ATTEMPTS,
    )
    _sha256("request_body_sha256", payload.get("request_body_sha256"))
    _sha256("prompt_sha256", payload.get("prompt_sha256"))
    _sha256("route_binding_sha256", payload.get("route_binding_sha256"))
    _sha256(
        "transaction_binding_sha256", payload.get("transaction_binding_sha256")
    )
    _sha256("accepted_start_payload_sha256", payload.get("accepted_start_payload_sha256"))
    response = payload.get("response")
    if not isinstance(response, Mapping):
        raise PilotCheckpointV3Error("call checkpoint response must be an object")
    _validate_response_payload(response)
    if payload.get("coordinator_version") != V3_COORDINATOR_VERSION:
        raise PilotCheckpointV3Error("call checkpoint coordinator version drifted")


def load_attempt_start(
    campaign_dir: str | Path,
    shard_index: int,
    slot_index: int,
    attempt_ordinal: int,
) -> dict[str, Any]:
    envelope = load_envelope(
        attempt_start_path(campaign_dir, shard_index, slot_index, attempt_ordinal),
        expected_kind="v3-physical-attempt-start",
    )
    _validate_start_payload(envelope["payload"])
    payload = envelope["payload"]
    if (
        payload["shard_index"] != shard_index
        or payload["slot_index"] != slot_index
        or payload["attempt_ordinal"] != attempt_ordinal
    ):
        raise PilotCheckpointV3Error("attempt start coordinates do not match its path")
    return envelope


def load_attempt_outcome(
    campaign_dir: str | Path,
    shard_index: int,
    slot_index: int,
    attempt_ordinal: int,
) -> dict[str, Any]:
    envelope = load_envelope(
        attempt_outcome_path(campaign_dir, shard_index, slot_index, attempt_ordinal),
        expected_kind="v3-physical-attempt-outcome",
    )
    _validate_outcome_payload(envelope["payload"])
    payload = envelope["payload"]
    if (
        payload["shard_index"] != shard_index
        or payload["slot_index"] != slot_index
        or payload["attempt_ordinal"] != attempt_ordinal
    ):
        raise PilotCheckpointV3Error("attempt outcome coordinates do not match its path")
    return envelope


def load_call_checkpoint(
    campaign_dir: str | Path,
    shard_index: int,
    slot_index: int,
) -> dict[str, Any]:
    envelope = load_envelope(
        call_checkpoint_path(campaign_dir, shard_index, slot_index),
        expected_kind="v3-logical-call-checkpoint",
    )
    _validate_call_payload(envelope["payload"])
    payload = envelope["payload"]
    if payload["shard_index"] != shard_index or payload["slot_index"] != slot_index:
        raise PilotCheckpointV3Error("call checkpoint coordinates do not match its path")
    return envelope


def inspect_slot_state(
    campaign_dir: str | Path,
    shard_index: int,
    slot_index: int,
) -> SlotRecoveryState:
    """Validate the complete marker chain and return its recovery status."""

    shard, slot = _indices(shard_index, slot_index)
    starts: dict[int, dict[str, Any]] = {}
    outcomes: dict[int, dict[str, Any]] = {}
    seen_gap = False
    for ordinal in range(1, MAX_PHYSICAL_ATTEMPTS + 1):
        start_path = attempt_start_path(campaign_dir, shard, slot, ordinal)
        outcome_path = attempt_outcome_path(campaign_dir, shard, slot, ordinal)
        if start_path.exists():
            if seen_gap:
                raise PilotCheckpointV3Error("physical attempt start sequence has a gap")
            starts[ordinal] = load_attempt_start(campaign_dir, shard, slot, ordinal)
        else:
            seen_gap = True
        if outcome_path.exists():
            if ordinal not in starts:
                raise PilotCheckpointV3Error("attempt outcome exists without its start")
            outcomes[ordinal] = load_attempt_outcome(campaign_dir, shard, slot, ordinal)
            if outcomes[ordinal]["payload"]["start_payload_sha256"] != starts[ordinal]["payload_sha256"]:
                raise PilotCheckpointV3Error("attempt outcome does not bind its start")

    call_path = call_checkpoint_path(campaign_dir, shard, slot)
    call = load_call_checkpoint(campaign_dir, shard, slot) if call_path.exists() else None
    if call is not None:
        payload = call["payload"]
        accepted = payload["accepted_attempt"]
        if accepted not in starts:
            raise PilotCheckpointV3Error("call checkpoint lacks its attempt start")
        if accepted in outcomes:
            raise PilotCheckpointV3Error("accepted attempt also has a failure outcome")
        start_payload = starts[accepted]["payload"]
        if payload["accepted_start_payload_sha256"] != starts[accepted]["payload_sha256"]:
            raise PilotCheckpointV3Error("call checkpoint does not bind its attempt start")
        for field in (
            "request_body_sha256",
            "prompt_sha256",
            "route_binding_sha256",
            "transaction_binding_sha256",
        ):
            if payload[field] != start_payload[field]:
                raise PilotCheckpointV3Error(f"call checkpoint {field} drifted from start")
        if tuple(starts) != tuple(range(1, accepted + 1)):
            raise PilotCheckpointV3Error("call checkpoint accepted attempt is not the start prefix")
        first_start_payload = starts[1]["payload"]
        for ordinal, start in starts.items():
            for field in (
                "request_body_sha256",
                "prompt_sha256",
                "route_binding_sha256",
                "transaction_binding_sha256",
            ):
                if start["payload"][field] != first_start_payload[field]:
                    raise PilotCheckpointV3Error(
                        f"attempt {ordinal} changed the prepared request binding"
                    )
        for ordinal in range(1, accepted):
            if ordinal not in outcomes or not outcomes[ordinal]["payload"]["outcome_class"].startswith("retryable_"):
                raise PilotCheckpointV3Error("pre-acceptance attempt was not durably retryable")
        return SlotRecoveryState(
            status="committed",
            next_attempt=None,
            started_attempts=tuple(starts),
            retryable_outcomes=tuple(
                ordinal
                for ordinal, value in outcomes.items()
                if value["payload"]["outcome_class"].startswith("retryable_")
            ),
            accepted_attempt=accepted,
            call_payload_sha256=call["payload_sha256"],
        )

    if not starts:
        return SlotRecoveryState("pristine", 1, (), ())

    latest = max(starts)
    if tuple(starts) != tuple(range(1, latest + 1)):
        raise PilotCheckpointV3Error("physical attempt start sequence is not a prefix")
    first_start_payload = starts[1]["payload"]
    for ordinal, start in starts.items():
        for field in (
            "request_body_sha256",
            "prompt_sha256",
            "route_binding_sha256",
            "transaction_binding_sha256",
        ):
            if start["payload"][field] != first_start_payload[field]:
                raise PilotCheckpointV3Error(
                    f"attempt {ordinal} changed the prepared request binding"
                )
    for ordinal in range(1, latest):
        if ordinal not in outcomes or not outcomes[ordinal]["payload"]["outcome_class"].startswith("retryable_"):
            raise PilotCheckpointV3Error("a later attempt lacks retry authorization")
    if latest not in outcomes:
        return SlotRecoveryState(
            "unresolved",
            None,
            tuple(starts),
            tuple(range(1, latest)),
        )
    latest_class = outcomes[latest]["payload"]["outcome_class"]
    retryable = tuple(
        ordinal
        for ordinal, value in outcomes.items()
        if value["payload"]["outcome_class"].startswith("retryable_")
    )
    if latest_class.startswith("fatal_"):
        return SlotRecoveryState(
            "fatal", None, tuple(starts), retryable, fatal_attempt=latest
        )
    if latest == MAX_PHYSICAL_ATTEMPTS:
        return SlotRecoveryState("exhausted", None, tuple(starts), retryable)
    return SlotRecoveryState(
        "ready_for_retry", latest + 1, tuple(starts), retryable
    )


def inspect_shard_prefix(
    campaign_dir: str | Path,
    shard_index: int,
) -> ShardRecoveryState:
    """Reject future-slot markers across the exact frozen 20-slot shard."""

    states = tuple(
        inspect_slot_state(campaign_dir, shard_index, slot_index)
        for slot_index in range(SLOTS_PER_SHARD)
    )
    prefix = 0
    while prefix < SLOTS_PER_SHARD and states[prefix].status == "committed":
        prefix += 1
    if prefix == SLOTS_PER_SHARD:
        return ShardRecoveryState(prefix, None, None, states)
    for slot_index in range(prefix + 1, SLOTS_PER_SHARD):
        if states[slot_index].status != "pristine":
            raise PilotCheckpointV3Error(
                "shard contains a future logical-slot marker beyond its commit prefix"
            )
    return ShardRecoveryState(prefix, prefix, states[prefix].status, states)


def validate_artifact_inventory(campaign_dir: str | Path) -> None:
    """Reject marker/checkpoint filenames outside the closed v3 grammar.

    Publication temporaries are not scientific state. If a process dies while
    one remains, the campaign stops for explicit inspection rather than
    silently treating an unknown file as irrelevant.
    """

    root = Path(campaign_dir)
    specifications = (
        (root / PHYSICAL_ATTEMPT_DIRECTORY_NAME, _ATTEMPT_FILE_PATTERN),
        (root / CALL_CHECKPOINT_DIRECTORY_NAME, _CALL_FILE_PATTERN),
    )
    touched_coordinates: set[tuple[int, int]] = set()
    for directory, pattern in specifications:
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise PilotCheckpointV3Error(
                "v3 artifact directory is not a real directory"
            )
        for path in directory.iterdir():
            match = pattern.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_file():
                raise PilotCheckpointV3Error(
                    "v3 artifact inventory contains an unknown file"
                )
            shard = int(match.group(1))
            slot = int(match.group(2))
            if not (0 <= shard < V3_TOTAL_SHARDS and 0 <= slot < SLOTS_PER_SHARD):
                raise PilotCheckpointV3Error(
                    "v3 artifact filename coordinates drifted"
                )
            touched_coordinates.add((shard, slot))
            if pattern is _ATTEMPT_FILE_PATTERN:
                attempt = int(match.group(3))
                if not 1 <= attempt <= MAX_PHYSICAL_ATTEMPTS:
                    raise PilotCheckpointV3Error(
                        "v3 physical attempt filename drifted"
                    )
                if match.group(4) == "start":
                    load_attempt_start(root, shard, slot, attempt)
                else:
                    load_attempt_outcome(root, shard, slot, attempt)
            else:
                load_call_checkpoint(root, shard, slot)
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                raise PilotCheckpointV3Error("v3 artifact file mode must be 0600")
    for shard, slot in sorted(touched_coordinates):
        inspect_slot_state(root, shard, slot)


def publish_attempt_start(
    campaign_dir: str | Path,
    *,
    shard_index: int,
    slot_index: int,
    attempt_ordinal: int,
    request_body_sha256: str,
    prompt_sha256: str,
    route_binding_sha256: str,
    transaction_binding_sha256: str,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    state = inspect_slot_state(campaign_dir, shard_index, slot_index)
    if state.status not in {"pristine", "ready_for_retry"} or state.next_attempt != attempt_ordinal:
        raise PilotCheckpointV3Error(
            f"attempt {attempt_ordinal} is not authorized from slot state {state.status}"
        )
    if state.status == "ready_for_retry":
        first = load_attempt_start(campaign_dir, shard_index, slot_index, 1)["payload"]
        for field, value in (
            ("request_body_sha256", request_body_sha256),
            ("prompt_sha256", prompt_sha256),
            ("route_binding_sha256", route_binding_sha256),
            ("transaction_binding_sha256", transaction_binding_sha256),
        ):
            if first[field] != value:
                raise PilotCheckpointV3Error(
                    f"retry changed the prepared request binding: {field}"
                )
    payload = {
        "shard_index": shard_index,
        "slot_index": slot_index,
        "attempt_ordinal": attempt_ordinal,
        "request_body_sha256": _sha256("request_body_sha256", request_body_sha256),
        "prompt_sha256": _sha256("prompt_sha256", prompt_sha256),
        "route_binding_sha256": _sha256("route_binding_sha256", route_binding_sha256),
        "transaction_binding_sha256": _sha256(
            "transaction_binding_sha256", transaction_binding_sha256
        ),
        "coordinator_version": V3_COORDINATOR_VERSION,
    }
    _validate_start_payload(payload)
    return publish_envelope(
        attempt_start_path(campaign_dir, shard_index, slot_index, attempt_ordinal),
        kind="v3-physical-attempt-start",
        payload=payload,
        forbidden_values=forbidden_values,
    )


def publish_attempt_outcome(
    campaign_dir: str | Path,
    *,
    shard_index: int,
    slot_index: int,
    attempt_ordinal: int,
    outcome_class: str,
    failure_category: str | None = None,
    http_status: int | None = None,
    known_input_tokens: int | None = None,
    known_output_tokens: int | None = None,
    known_latency_ms: float | None = None,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    state = inspect_slot_state(campaign_dir, shard_index, slot_index)
    if state.status != "unresolved" or state.started_attempts[-1:] != (attempt_ordinal,):
        raise PilotCheckpointV3Error("only the current unresolved attempt may receive an outcome")
    start = load_attempt_start(campaign_dir, shard_index, slot_index, attempt_ordinal)
    payload = {
        "shard_index": shard_index,
        "slot_index": slot_index,
        "attempt_ordinal": attempt_ordinal,
        "start_payload_sha256": start["payload_sha256"],
        "outcome_class": outcome_class,
        "failure_category": failure_category,
        "http_status": http_status,
        "known_input_tokens": known_input_tokens,
        "known_output_tokens": known_output_tokens,
        "known_latency_ms": known_latency_ms,
    }
    _validate_outcome_payload(payload)
    return publish_envelope(
        attempt_outcome_path(campaign_dir, shard_index, slot_index, attempt_ordinal),
        kind="v3-physical-attempt-outcome",
        payload=payload,
        forbidden_values=forbidden_values,
    )


def publish_call_checkpoint(
    campaign_dir: str | Path,
    payload: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    # Detach nested mutable/custom mappings before the first validation.  A
    # shallow copy would permit a caller or another thread to change response
    # content between validation and immutable publication.
    import json

    try:
        detached = json.loads(canonical_json_bytes(payload).decode("utf-8"))
    except Exception as exc:
        raise PilotCheckpointV3Error("call checkpoint must be finite plain JSON") from exc
    if not isinstance(detached, Mapping):
        raise PilotCheckpointV3Error("call checkpoint must be an object")
    _validate_call_payload(detached)
    shard = detached["shard_index"]
    slot = detached["slot_index"]
    accepted = detached["accepted_attempt"]
    state = inspect_slot_state(campaign_dir, shard, slot)
    if state.status != "unresolved" or state.started_attempts[-1:] != (accepted,):
        raise PilotCheckpointV3Error("call checkpoint must resolve the current attempt")
    start = load_attempt_start(campaign_dir, shard, slot, accepted)
    if detached["accepted_start_payload_sha256"] != start["payload_sha256"]:
        raise PilotCheckpointV3Error("call checkpoint start binding is incorrect")
    for field in (
        "request_body_sha256",
        "prompt_sha256",
        "route_binding_sha256",
        "transaction_binding_sha256",
    ):
        if detached[field] != start["payload"][field]:
            raise PilotCheckpointV3Error(f"call checkpoint {field} mismatches attempt start")
    return publish_envelope(
        call_checkpoint_path(campaign_dir, shard, slot),
        kind="v3-logical-call-checkpoint",
        payload=detached,
        forbidden_values=forbidden_values,
    )


__all__ = [
    "CALL_CHECKPOINT_DIRECTORY_NAME",
    "MAX_PHYSICAL_ATTEMPTS",
    "PHYSICAL_ATTEMPT_DIRECTORY_NAME",
    "PilotCheckpointV3Error",
    "SHARD_LOCK_DIRECTORY_NAME",
    "SLOTS_PER_SHARD",
    "SlotRecoveryState",
    "ShardRecoveryState",
    "V3_COORDINATOR_VERSION",
    "V3_TOTAL_SHARDS",
    "acquire_shard_lock",
    "attempt_outcome_path",
    "attempt_start_path",
    "call_checkpoint_path",
    "inspect_slot_state",
    "inspect_shard_prefix",
    "load_attempt_outcome",
    "load_attempt_start",
    "load_call_checkpoint",
    "publish_attempt_outcome",
    "publish_attempt_start",
    "publish_call_checkpoint",
    "shard_lock_path",
    "validate_artifact_inventory",
]
