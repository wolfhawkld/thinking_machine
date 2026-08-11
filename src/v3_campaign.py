"""Offline campaign storage and generation barriers for V3.

This module owns the immutable campaign-wide state above the per-logical-slot
checkpoints in :mod:`src.pilot_checkpoint_v3`.  It performs no network I/O and
never evaluates a private test.  A live coordinator must hold the global
campaign frontier lock for the complete duration of an episode, then publish
that episode's seal while the same lease is active.

Only hashes of call/attempt artifacts and aggregate generation diagnostics are
copied into an episode seal.  Candidate expressions, candidate identity
hashes, prompts, endpoints, raw responses, and private-test material are
deliberately excluded.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from .pilot_checkpoint import (
    PilotCheckpointError,
    canonical_json_bytes,
    load_envelope,
    publish_envelope,
    sha256_bytes,
    sha256_json,
)
from .pilot_checkpoint_v3 import (
    CALL_CHECKPOINT_DIRECTORY_NAME,
    MAX_PHYSICAL_ATTEMPTS,
    PHYSICAL_ATTEMPT_DIRECTORY_NAME,
    SHARD_LOCK_DIRECTORY_NAME,
    SLOTS_PER_SHARD,
    attempt_outcome_path,
    attempt_start_path,
    call_checkpoint_path,
    inspect_shard_prefix,
    inspect_slot_state,
    load_attempt_outcome,
    load_attempt_start,
    load_call_checkpoint,
    validate_artifact_inventory,
)
from .provenance import PROJECT_ROOT, source_manifest
from .runner import CANDIDATE_FORMATS
from .staged_pilot_v3 import (
    AcceptedResponseContract,
    FrozenTransactionIdentity,
    V3_ACCEPTED_ATTEMPT_ESTIMAND,
)
from .v3_development import (
    V3_ARM_IDS,
    V3_CALLS_PER_SHARD,
    V3_CAMPAIGN_MANIFEST_KIND,
    V3_GATE_SHARDS,
    V3_MAIN_SHARDS,
    V3_MODEL_STRATA,
    V3_TOTAL_SHARDS,
    build_campaign_manifest,
    validate_campaign_manifest,
)


CAMPAIGN_MANIFEST_NAME = "v3-campaign-manifest.json"
EPISODE_SEAL_DIRECTORY_NAME = "episode-seals"
CAMPAIGN_LOCK_DIRECTORY_NAME = "campaign-locks"
CAMPAIGN_LOCK_NAME = "global.lock"
COMPATIBILITY_SCREEN_NAME = "compatibility-screen.json"
GENERATION_BARRIER_NAME = "generation-barrier.json"
FINALIZED_SNAPSHOT_NAME = "v3-finalized-snapshot.json"

EPISODE_SEAL_KIND = "v3-generation-episode-seal"
COMPATIBILITY_SCREEN_KIND = "v3-route-compatibility-screen"
GENERATION_BARRIER_KIND = "v3-generation-barrier"

_SEAL_PATTERN = re.compile(r"^shard-([0-9]{4})\.json$")
_ATTEMPT_PATTERN = re.compile(
    r"^shard-([0-9]{4})-slot-([0-9]{2})-attempt-([0-9]{2})-(start|outcome)\.json$"
)
_CALL_PATTERN = re.compile(r"^shard-([0-9]{4})-slot-([0-9]{2})\.json$")
_SHARD_LOCK_PATTERN = re.compile(r"^shard-([0-9]{4})\.lock$")

_EPISODE_METRIC_KEYS = frozenset(
    {
        "planned_candidate_count",
        "completed_candidate_count",
        "outer_schema_valid_count",
        "search_valid_count",
        "canonical_unique_count",
        "behavioral_unique_count",
        "all_invalid",
        "selection_exists",
        "selected_probe_score",
        "round_best_scores",
        "failure_counts",
        "candidate_format_counts",
        "temperature_trajectory",
        "slot_temperature_trajectory",
        "controller_trace",
        "generation_state_sha256",
        "private_test_evaluated",
    }
)
_FAILURE_COUNT_KEYS = frozenset(
    {
        "total_candidates",
        "syntax_failures",
        "runtime_failures",
        "invalid_candidates",
        "records_with_errors",
        "by_code",
    }
)
_FAILURE_CODE_KEYS = frozenset(
    {"parse_or_grammar", "depth", "node_count", "output_bound", "runtime"}
)
_E2_TRACE_KEYS = frozenset(
    {
        "controller_version",
        "round_index",
        "round_best",
        "best_score",
        "pre_round_best_score",
        "improved",
        "planned_candidate_count",
        "valid_candidate_count",
        "new_behavior_count",
        "useful_new_behavior_count",
        "decision",
        "decision_reason",
        "previous_temperature",
        "next_temperature",
    }
)
_ATTEMPT_ARTIFACT_KEYS = frozenset(
    {
        "slot_index",
        "attempt_ordinal",
        "start_payload_sha256",
        "outcome_payload_sha256",
    }
)
_CALL_FILE_REFERENCE_KEYS = frozenset(
    {
        "slot_index",
        "checkpoint_file",
        "checkpoint_file_sha256",
        "checkpoint_payload_sha256",
    }
)
_AUDIT_KEYS = frozenset(
    {
        "estimand",
        "durable_logical_call_checkpoints",
        "shard_complete",
        "physical_request_starts",
        "start_markers_are_not_confirmed_provider_receipts",
        "slots_with_retry",
        "retry_count",
        "outcome_class_counts",
        "failure_category_counts",
        "http_status_counts",
        "unresolved_slot_count",
        "exhausted_slot_count",
        "fatal_slot_count",
        "ready_for_retry_slot_count",
        "accepted_attempt_ordinals",
        "content_retry_count",
        "accepted_known_input_tokens",
        "accepted_known_output_tokens",
        "accepted_known_latency_ms",
        "discarded_known_response_count",
        "discarded_known_input_tokens",
        "discarded_known_output_tokens",
        "discarded_known_latency_ms",
        "known_usage_response_count",
        "usage_unknown_start_marker_count",
        "gross_known_token_lower_bound",
        "gross_known_latency_ms",
        "gross_usage_complete",
        "recovery_allows_actual_token_matched_claim",
    }
)
_RUNTIME_AUDIT_KEYS = _AUDIT_KEYS | {
    "logical_calls_seen",
    "physical_request_start_markers",
    "call_checkpoint_replays",
}
_SEAL_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "campaign_manifest_payload_sha256",
        "execution_plan_sha256",
        "plan_entry_sha256",
        "run_id",
        "shard_index",
        "phase",
        "model_stratum",
        "route_binding_sha256",
        "transaction_binding_sha256",
        "generation_state_sha256",
        "logical_calls_completed",
        "ordered_call_checkpoint_payload_sha256",
        "ordered_call_checkpoints",
        "ordered_attempt_artifacts",
        "episode_metrics",
        "execution_audit",
        "private_test_evaluated",
    }
)
_ARM_SCREEN_KEYS = frozenset(
    {
        "arm_id",
        "planned_call_count",
        "search_valid_count",
        "canonical_unique_count",
        "behavioral_unique_count",
        "validity_passed",
    }
)
_ROUTE_SCREEN_KEYS = frozenset(
    {
        "model_stratum",
        "planned_call_count",
        "search_valid_count",
        "minimum_overall_search_valid_count",
        "minimum_per_arm_search_valid_count",
        "arms",
        "h_minus_l_canonical_unique_count",
        "h_minus_l_behavioral_unique_count",
        "passed",
        "failure_codes",
    }
)
_SCREEN_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "campaign_manifest_payload_sha256",
        "execution_plan_sha256",
        "ordered_gate_seal_payload_sha256",
        "gate_shard_count",
        "gate_logical_calls_completed",
        "routes",
        "both_routes_passed",
        "status",
        "private_test_evaluated",
    }
)
_INVENTORY_ITEM_KEYS = frozenset(
    {"path", "kind", "file_sha256", "payload_sha256"}
)
_BARRIER_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "campaign_manifest_payload_sha256",
        "execution_plan_sha256",
        "compatibility_screen_payload_sha256",
        "ordered_episode_seal_payload_sha256",
        "gate_shard_count",
        "main_shard_count",
        "main_logical_calls_completed",
        "total_logical_calls_completed",
        "artifact_inventory",
        "artifact_inventory_sha256",
        "generation_complete",
        "private_test_evaluated",
    }
)


class V3CampaignError(PilotCheckpointError):
    """Raised when immutable V3 campaign state is incomplete or inconsistent."""


@dataclass
class CampaignLockLease:
    """An active process-local proof that the global campaign lock is held."""

    campaign_dir: Path
    _descriptor: int = field(repr=False)
    active: bool = True


def _detached(value: Any, name: str) -> Any:
    try:
        return json.loads(canonical_json_bytes(value).decode("utf-8"))
    except Exception as exc:
        raise V3CampaignError(f"{name} must be finite plain JSON") from exc


def _exact_keys(name: str, value: Any, expected: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise V3CampaignError(f"{name} must be an object")
    actual = frozenset(str(key) for key in value)
    if actual != expected:
        raise V3CampaignError(
            f"{name} keys drifted: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )
    return value


def _sha256(name: str, value: Any) -> str:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise V3CampaignError(f"{name} must be a lowercase SHA-256")
    return value


def _integer(name: str, value: Any, *, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise V3CampaignError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _number(name: str, value: Any, *, low: float = 0.0, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise V3CampaignError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < low or (high is not None and result > high):
        raise V3CampaignError(f"{name} is outside its finite range")
    return result


def _root(campaign_dir: str | Path, *, create: bool = False) -> Path:
    path = Path(campaign_dir)
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        info = path.lstat()
    except OSError as exc:
        raise V3CampaignError("campaign directory is unavailable") from exc
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise V3CampaignError("campaign directory must be a real directory")
    return path.resolve()


def campaign_manifest_path(campaign_dir: str | Path) -> Path:
    return Path(campaign_dir) / CAMPAIGN_MANIFEST_NAME


def episode_seal_path(campaign_dir: str | Path, shard_index: int) -> Path:
    shard = _integer("shard_index", shard_index, low=0, high=V3_TOTAL_SHARDS - 1)
    return Path(campaign_dir) / EPISODE_SEAL_DIRECTORY_NAME / f"shard-{shard:04d}.json"


def compatibility_screen_path(campaign_dir: str | Path) -> Path:
    return Path(campaign_dir) / COMPATIBILITY_SCREEN_NAME


def generation_barrier_path(campaign_dir: str | Path) -> Path:
    return Path(campaign_dir) / GENERATION_BARRIER_NAME


def campaign_lock_path(campaign_dir: str | Path) -> Path:
    return Path(campaign_dir) / CAMPAIGN_LOCK_DIRECTORY_NAME / CAMPAIGN_LOCK_NAME


def _assert_lease(campaign_dir: str | Path, lease: CampaignLockLease) -> None:
    if not isinstance(lease, CampaignLockLease) or not lease.active:
        raise V3CampaignError("an active campaign lock lease is required")
    if lease.campaign_dir != _root(campaign_dir):
        raise V3CampaignError("campaign lock lease belongs to another directory")
    try:
        if not stat.S_ISREG(os.fstat(lease._descriptor).st_mode):
            raise V3CampaignError("campaign lock descriptor is no longer regular")
    except OSError as exc:
        raise V3CampaignError("campaign lock lease is no longer active") from exc


@contextmanager
def acquire_campaign_lock(
    campaign_dir: str | Path,
    *,
    blocking: bool = False,
) -> Iterator[CampaignLockLease]:
    """Acquire the nonblocking-by-default campaign-wide serialization lock."""

    if type(blocking) is not bool:
        raise TypeError("blocking must be a boolean")
    root = _root(campaign_dir, create=True)
    path = campaign_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise V3CampaignError("campaign lock directory must be real")
        os.chmod(path.parent, 0o700)
    except OSError as exc:
        raise V3CampaignError("cannot secure campaign lock directory") from exc
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise V3CampaignError("cannot safely open campaign lock") from exc
    lease = CampaignLockLease(root, descriptor)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise V3CampaignError("campaign lock must be a regular file")
        os.fchmod(descriptor, 0o600)
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as exc:
            raise V3CampaignError("campaign is already owned by another process") from exc
        yield lease
    finally:
        lease.active = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@lru_cache(maxsize=8)
def _validated_manifest_canonical(encoded: bytes) -> bytes:
    """Memoize deterministic validation of a byte-identical frozen manifest."""

    try:
        value = json.loads(encoded.decode("utf-8"))
        validated = validate_campaign_manifest(value)
        return canonical_json_bytes(validated)
    except Exception as exc:
        raise V3CampaignError("campaign manifest payload is invalid") from exc


def _manifest_payload(value: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    detached = _detached(value, "campaign manifest")
    envelope_keys = {"schema_version", "kind", "payload_sha256", "payload"}
    if isinstance(detached, Mapping) and set(detached) == envelope_keys:
        if detached.get("kind") != V3_CAMPAIGN_MANIFEST_KIND:
            raise V3CampaignError("campaign manifest envelope kind drifted")
        payload = detached.get("payload")
        if not isinstance(payload, Mapping):
            raise V3CampaignError("campaign manifest envelope payload is not an object")
        digest = _sha256("campaign manifest payload hash", detached.get("payload_sha256"))
        if digest != sha256_json(payload):
            raise V3CampaignError("campaign manifest envelope hash drifted")
    else:
        payload = detached
        digest = sha256_json(payload)
    encoded = canonical_json_bytes(payload)
    validated = json.loads(_validated_manifest_canonical(encoded).decode("utf-8"))
    return validated, digest


def _validate_current_source(
    manifest: Mapping[str, Any], current_source_manifest: Mapping[str, Any]
) -> None:
    current = _detached(current_source_manifest, "current source manifest")
    expected_keys = {
        "schema_version",
        "created_at_utc",
        "source_manifest_sha256",
        "files",
        "environment",
    }
    if not isinstance(current, Mapping) or set(current) != expected_keys:
        raise V3CampaignError("current source manifest schema drifted")
    files = current.get("files")
    if not isinstance(files, list) or not files:
        raise V3CampaignError("current source manifest file list is invalid")
    digest = _sha256("current source manifest hash", current.get("source_manifest_sha256"))
    if sha256_json(files) != digest:
        raise V3CampaignError("current source manifest file-list hash drifted")
    if digest != manifest["source_manifest_sha256"]:
        raise V3CampaignError("current source tree differs from frozen campaign")
    if files != manifest["source_manifest"]["files"]:
        raise V3CampaignError("current source file inventory differs from campaign")


def publish_campaign_manifest(
    campaign_dir: str | Path,
    config: Mapping[str, Any],
    execution_plan: Sequence[Mapping[str, Any]],
    *,
    current_source_manifest: Mapping[str, Any] | None = None,
    forbidden_values: Sequence[str] = (),
    lease: CampaignLockLease | None = None,
) -> dict[str, Any]:
    """Build and exclusively publish the self-contained frozen manifest."""

    if lease is None:
        with acquire_campaign_lock(campaign_dir) as owned:
            return publish_campaign_manifest(
                campaign_dir,
                config,
                execution_plan,
                current_source_manifest=current_source_manifest,
                forbidden_values=forbidden_values,
                lease=owned,
            )
    _assert_lease(campaign_dir, lease)
    current = (
        source_manifest(PROJECT_ROOT)
        if current_source_manifest is None
        else current_source_manifest
    )
    try:
        payload = build_campaign_manifest(
            config,
            execution_plan,
            source_manifest=current,
        )
        payload = validate_campaign_manifest(payload)
    except Exception as exc:
        raise V3CampaignError("cannot build a live-eligible campaign manifest") from exc
    _validate_current_source(payload, current)
    root = _root(campaign_dir)
    allowed_before_manifest = {CAMPAIGN_LOCK_DIRECTORY_NAME}
    if any(path.name not in allowed_before_manifest for path in root.iterdir()):
        raise V3CampaignError("campaign directory is not pristine before manifest publication")
    return publish_envelope(
        campaign_manifest_path(root),
        kind=V3_CAMPAIGN_MANIFEST_KIND,
        payload=payload,
        forbidden_values=forbidden_values,
    )


def load_campaign_manifest(
    campaign_dir: str | Path,
    *,
    current_source_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load the immutable manifest and compare it with the current source tree."""

    root = _root(campaign_dir)
    try:
        envelope = load_envelope(
            campaign_manifest_path(root),
            expected_kind=V3_CAMPAIGN_MANIFEST_KIND,
        )
        payload = validate_campaign_manifest(envelope["payload"])
    except Exception as exc:
        raise V3CampaignError("cannot load the frozen V3 campaign manifest") from exc
    current = (
        source_manifest(PROJECT_ROOT)
        if current_source_manifest is None
        else current_source_manifest
    )
    _validate_current_source(payload, current)
    return envelope


def _assert_stored_manifest(
    campaign_dir: str | Path, manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], str]:
    payload, digest = _manifest_payload(manifest)
    try:
        stored = load_envelope(
            campaign_manifest_path(campaign_dir),
            expected_kind=V3_CAMPAIGN_MANIFEST_KIND,
        )
    except Exception as exc:
        raise V3CampaignError("campaign does not contain its frozen manifest") from exc
    if stored["payload_sha256"] != digest or stored["payload"] != payload:
        raise V3CampaignError("supplied campaign manifest differs from durable manifest")
    return payload, digest


def _sensitive_seal_scan(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            # ``candidate_format_counts`` is already validated against this
            # closed enum.  Several enum labels contain the word
            # ``expression`` but are aggregate categories, not persisted DSL.
            if normalized in CANDIDATE_FORMATS | {"unavailable"}:
                pass
            elif normalized == "private_test_evaluated":
                if item is not False:
                    raise V3CampaignError("private test cannot be evaluated before the barrier")
            elif any(token in normalized for token in ("expression", "prompt", "endpoint", "raw", "private")):
                raise V3CampaignError(f"episode seal contains forbidden field {key!r}")
            elif "candidate" in normalized and "hash" in normalized:
                raise V3CampaignError("episode seal cannot contain a candidate identity hash")
            _sensitive_seal_scan(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _sensitive_seal_scan(item)


def _validate_metric_counts(metrics: Mapping[str, Any]) -> None:
    planned = _integer("planned_candidate_count", metrics["planned_candidate_count"], low=20, high=20)
    completed = _integer("completed_candidate_count", metrics["completed_candidate_count"], low=20, high=20)
    del completed
    outer = _integer("outer_schema_valid_count", metrics["outer_schema_valid_count"], low=0, high=planned)
    valid = _integer("search_valid_count", metrics["search_valid_count"], low=0, high=planned)
    canonical = _integer("canonical_unique_count", metrics["canonical_unique_count"], low=0, high=valid)
    behavioral = _integer("behavioral_unique_count", metrics["behavioral_unique_count"], low=0, high=valid)
    del canonical, behavioral
    if type(metrics["all_invalid"]) is not bool or metrics["all_invalid"] is not (valid == 0):
        raise V3CampaignError("all_invalid does not match search-valid count")
    if type(metrics["selection_exists"]) is not bool or metrics["selection_exists"] is not (valid > 0):
        raise V3CampaignError("selection existence does not match search-valid count")
    selected_score = metrics["selected_probe_score"]
    if valid == 0:
        if selected_score is not None:
            raise V3CampaignError("all-invalid episode cannot have a selected score")
    else:
        _number("selected_probe_score", selected_score, high=1.0)

    failures = _exact_keys("failure_counts", metrics["failure_counts"], _FAILURE_COUNT_KEYS)
    counts = {
        name: _integer(f"failure_counts.{name}", failures[name], low=0, high=20)
        for name in (
            "total_candidates",
            "syntax_failures",
            "runtime_failures",
            "invalid_candidates",
            "records_with_errors",
        )
    }
    if counts["total_candidates"] != 20:
        raise V3CampaignError("failure count total must equal 20")
    if counts["invalid_candidates"] != counts["syntax_failures"] + counts["runtime_failures"]:
        raise V3CampaignError("invalid candidate count decomposition drifted")
    if valid + counts["invalid_candidates"] != 20:
        raise V3CampaignError("valid and invalid candidate counts do not close")
    by_code = _exact_keys("failure_counts.by_code", failures["by_code"], _FAILURE_CODE_KEYS)
    for name, value in by_code.items():
        _integer(f"failure_counts.by_code.{name}", value, low=0, high=20)

    formats = metrics["candidate_format_counts"]
    if not isinstance(formats, Mapping) or not formats:
        raise V3CampaignError("candidate format counts must be a non-empty object")
    if any(key not in CANDIDATE_FORMATS | {"unavailable"} for key in formats):
        raise V3CampaignError("candidate format count contains an unknown class")
    normalized_formats = {
        str(key): _integer(f"candidate_format_counts.{key}", value, low=0, high=20)
        for key, value in formats.items()
    }
    if sum(normalized_formats.values()) != 20:
        raise V3CampaignError("candidate format counts do not sum to 20")
    if normalized_formats.get("json_expression", 0) != outer:
        raise V3CampaignError("outer schema-valid count disagrees with format counts")


def _validate_trace_and_temperatures(
    manifest: Mapping[str, Any], entry: Mapping[str, Any], metrics: Mapping[str, Any]
) -> None:
    trajectory = metrics["temperature_trajectory"]
    slots = metrics["slot_temperature_trajectory"]
    if not isinstance(trajectory, list) or len(trajectory) != 5:
        raise V3CampaignError("temperature trajectory must contain five rounds")
    temperatures = [
        _number(f"temperature_trajectory[{index}]", value, low=0.2, high=1.2)
        for index, value in enumerate(trajectory)
    ]
    if not isinstance(slots, list) or len(slots) != 5:
        raise V3CampaignError("slot temperature trajectory must contain five rounds")
    for round_index, row in enumerate(slots):
        if not isinstance(row, list) or len(row) != 4:
            raise V3CampaignError("each slot temperature row must contain four values")
        observed = [
            _number(
                f"slot_temperature_trajectory[{round_index}][{slot_index}]",
                value,
                low=0.2,
                high=1.2,
            )
            for slot_index, value in enumerate(row)
        ]
        if observed != [temperatures[round_index]] * 4:
            raise V3CampaignError("V3 arms must expose one temperature per round")
    trace = metrics["controller_trace"]
    if not isinstance(trace, list):
        raise V3CampaignError("controller trace must be an array")
    arm_id = entry["arm_id"]
    if arm_id == "L" and temperatures != [0.2] * 5:
        raise V3CampaignError("L temperature schedule drifted")
    if arm_id == "H" and temperatures != [1.2] * 5:
        raise V3CampaignError("H temperature schedule drifted")
    if arm_id == "C" and temperatures != [1.2, 0.2, 1.2, 0.2, 0.2]:
        raise V3CampaignError("C temperature schedule drifted")
    if arm_id != "E2":
        if trace:
            raise V3CampaignError("non-E2 episode cannot contain an E2 trace")
        return
    if len(trace) != 5 or temperatures[0] != 1.0:
        raise V3CampaignError("E2 requires a five-row trace beginning at 1.0")
    from .experiment import _policy_from_config

    policy = _policy_from_config(
        "E2", manifest["frozen_config"]["arms"]["E2"]
    )
    for index, raw in enumerate(trace):
        row = _exact_keys(f"controller_trace[{index}]", raw, _E2_TRACE_KEYS)
        for name, value in row.items():
            if type(value) not in {str, int, float, bool}:
                raise V3CampaignError(f"controller trace field {name} is not scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise V3CampaignError(f"controller trace field {name} is not finite")
        if row["round_index"] != index or float(row["previous_temperature"]) != temperatures[index]:
            raise V3CampaignError("E2 trace round/temperature binding drifted")
        update_inputs = {
            name: row[name]
            for name in (
                "round_index",
                "round_best",
                "best_score",
                "pre_round_best_score",
                "improved",
                "planned_candidate_count",
                "valid_candidate_count",
                "new_behavior_count",
                "useful_new_behavior_count",
            )
        }
        try:
            expected = policy.update(**update_inputs)
        except Exception as exc:
            raise V3CampaignError("E2 controller trace is not replayable") from exc
        if dict(row) != expected:
            raise V3CampaignError("E2 controller trace changed the frozen transition")
        if index < 4 and float(row["next_temperature"]) != temperatures[index + 1]:
            raise V3CampaignError("E2 next temperature does not bind the next round")


def _validate_episode_metrics(
    manifest: Mapping[str, Any], entry: Mapping[str, Any], value: Any
) -> dict[str, Any]:
    metrics = _detached(value, "episode metrics")
    _exact_keys("episode metrics", metrics, _EPISODE_METRIC_KEYS)
    if metrics["private_test_evaluated"] is not False:
        raise V3CampaignError("episode metrics contain private-test state")
    _sha256("generation_state_sha256", metrics["generation_state_sha256"])
    _validate_metric_counts(metrics)
    round_best = metrics["round_best_scores"]
    if not isinstance(round_best, list) or len(round_best) != 5:
        raise V3CampaignError("round-best scores must contain five values")
    for index, score in enumerate(round_best):
        _number(f"round_best_scores[{index}]", score, high=1.0)
    _validate_trace_and_temperatures(manifest, entry, metrics)
    _sensitive_seal_scan(metrics)
    return metrics


def _response_contract(
    manifest: Mapping[str, Any], model_stratum: str
) -> AcceptedResponseContract:
    matches = [
        item
        for item in manifest["route_contracts"]
        if item["model_stratum"] == model_stratum
    ]
    if len(matches) != 1:
        raise V3CampaignError("episode route is absent or duplicated in manifest")
    stored = matches[0]["route_contract"]["accepted_response_contract"]
    fingerprint = stored["provider_fingerprint_sha256"]
    if stored["provider_fingerprint_mode"] == "absent":
        fingerprint = None
    try:
        return AcceptedResponseContract(
            provider_models=tuple(stored["provider_models"]),
            finish_reasons=tuple(stored["finish_reasons"]),
            max_output_tokens=stored["max_output_tokens"],
            seed_supported=stored["seed_supported"],
            require_zero_reasoning_tokens=stored["require_zero_reasoning_tokens"],
            prompt_cache_mode=stored["prompt_cache_mode"],
            provider_fingerprint_mode=stored["provider_fingerprint_mode"],
            provider_fingerprint_sha256=fingerprint,
        )
    except Exception as exc:
        raise V3CampaignError("manifest accepted-response contract is invalid") from exc


def _transaction_identity(
    manifest: Mapping[str, Any], entry: Mapping[str, Any]
) -> FrozenTransactionIdentity:
    try:
        return FrozenTransactionIdentity.from_plan_entry(
            campaign_manifest_payload_sha256=sha256_json(manifest),
            execution_plan_sha256=manifest["execution_plan_sha256"],
            entry=entry,
        )
    except Exception as exc:
        raise V3CampaignError("cannot derive the frozen shard transaction") from exc


def _episode_artifacts(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
) -> tuple[
    list[str],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    shard_index = entry["shard_index"]
    identity = _transaction_identity(manifest, entry)
    route_binding = entry["route_binding_sha256"]
    contract = _response_contract(manifest, entry["model_stratum"])
    call_hashes: list[str] = []
    call_references: list[dict[str, Any]] = []
    attempt_artifacts: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    states: list[Any] = []
    outcomes: dict[tuple[int, int], Mapping[str, Any]] = {}
    for slot_index in range(SLOTS_PER_SHARD):
        state = inspect_slot_state(campaign_dir, shard_index, slot_index)
        if state.status != "committed":
            raise V3CampaignError("episode contains a noncommitted logical slot")
        states.append(state)
        call = load_call_checkpoint(campaign_dir, shard_index, slot_index)
        payload = call["payload"]
        if (
            payload["route_binding_sha256"] != route_binding
            or payload["transaction_binding_sha256"] != identity.binding_sha256
        ):
            raise V3CampaignError("logical call belongs to a foreign route or transaction")
        try:
            contract.validate_checkpoint_payload(payload["response"])
        except Exception as exc:
            raise V3CampaignError("logical call violates the frozen response contract") from exc
        call_hashes.append(call["payload_sha256"])
        checkpoint_file = call_checkpoint_path(campaign_dir, shard_index, slot_index)
        call_references.append(
            {
                "slot_index": slot_index,
                "checkpoint_file": checkpoint_file.relative_to(
                    Path(campaign_dir)
                ).as_posix(),
                "checkpoint_file_sha256": sha256_bytes(checkpoint_file.read_bytes()),
                "checkpoint_payload_sha256": call["payload_sha256"],
            }
        )
        calls.append(payload)
        for ordinal in state.started_attempts:
            start = load_attempt_start(campaign_dir, shard_index, slot_index, ordinal)
            outcome_path = attempt_outcome_path(
                campaign_dir, shard_index, slot_index, ordinal
            )
            outcome_hash = None
            if outcome_path.exists():
                outcome = load_attempt_outcome(
                    campaign_dir, shard_index, slot_index, ordinal
                )
                outcome_hash = outcome["payload_sha256"]
                outcomes[(slot_index, ordinal)] = outcome["payload"]
            attempt_artifacts.append(
                {
                    "slot_index": slot_index,
                    "attempt_ordinal": ordinal,
                    "start_payload_sha256": start["payload_sha256"],
                    "outcome_payload_sha256": outcome_hash,
                }
            )
    audit = _expected_execution_audit(
        campaign_dir,
        entry,
        calls,
        states=states,
        outcomes=outcomes,
    )
    return call_hashes, call_references, attempt_artifacts, calls, audit


def _expected_execution_audit(
    campaign_dir: str | Path,
    entry: Mapping[str, Any],
    calls: Sequence[Mapping[str, Any]],
    *,
    states: Sequence[Any] | None = None,
    outcomes: Mapping[tuple[int, int], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    shard = entry["shard_index"]
    if states is None:
        states = [inspect_slot_state(campaign_dir, shard, slot) for slot in range(20)]
    physical_starts = sum(len(state.started_attempts) for state in states)
    slots_with_retry = sum(len(state.started_attempts) > 1 for state in states)
    retry_count = physical_starts - 20
    outcome_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    http_counts: dict[str, int] = {}
    for slot, state in enumerate(states):
        for ordinal in state.retryable_outcomes:
            outcome = (
                outcomes[(slot, ordinal)]
                if outcomes is not None
                else load_attempt_outcome(campaign_dir, shard, slot, ordinal)["payload"]
            )
            outcome_class = outcome["outcome_class"]
            outcome_counts[outcome_class] = outcome_counts.get(outcome_class, 0) + 1
            category = outcome["failure_category"]
            if category is not None:
                category_counts[category] = category_counts.get(category, 0) + 1
            status = outcome["http_status"]
            if status is not None:
                key = str(status)
                http_counts[key] = http_counts.get(key, 0) + 1
    accepted_input = sum(int(call["response"]["input_tokens"]) for call in calls)
    accepted_output = sum(int(call["response"]["output_tokens"]) for call in calls)
    accepted_latency = sum(float(call["response"]["latency_ms"]) for call in calls)
    accepted_ordinals = [int(call["accepted_attempt"]) for call in calls]
    clean = retry_count == 0
    return {
        "estimand": V3_ACCEPTED_ATTEMPT_ESTIMAND,
        "durable_logical_call_checkpoints": 20,
        "shard_complete": True,
        "physical_request_starts": physical_starts,
        "start_markers_are_not_confirmed_provider_receipts": True,
        "slots_with_retry": slots_with_retry,
        "retry_count": retry_count,
        "outcome_class_counts": dict(sorted(outcome_counts.items())),
        "failure_category_counts": dict(sorted(category_counts.items())),
        "http_status_counts": dict(sorted(http_counts.items())),
        "unresolved_slot_count": 0,
        "exhausted_slot_count": 0,
        "fatal_slot_count": 0,
        "ready_for_retry_slot_count": 0,
        "accepted_attempt_ordinals": accepted_ordinals,
        "content_retry_count": 0,
        "accepted_known_input_tokens": accepted_input,
        "accepted_known_output_tokens": accepted_output,
        "accepted_known_latency_ms": accepted_latency,
        "discarded_known_response_count": 0,
        "discarded_known_input_tokens": 0,
        "discarded_known_output_tokens": 0,
        "discarded_known_latency_ms": 0.0,
        "known_usage_response_count": 20,
        "usage_unknown_start_marker_count": retry_count,
        "gross_known_token_lower_bound": accepted_input + accepted_output,
        "gross_known_latency_ms": accepted_latency,
        "gross_usage_complete": clean,
        "recovery_allows_actual_token_matched_claim": clean,
    }


def _validate_execution_audit(
    value: Any,
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    audit = _detached(value, "execution audit")
    actual_keys = frozenset(str(key) for key in audit)
    if actual_keys == _RUNTIME_AUDIT_KEYS:
        if audit["logical_calls_seen"] != 20:
            raise V3CampaignError("runtime audit did not visit exactly 20 calls")
        if audit["physical_request_start_markers"] != audit["physical_request_starts"]:
            raise V3CampaignError("runtime start-marker count drifted")
        _integer(
            "call_checkpoint_replays",
            audit["call_checkpoint_replays"],
            low=0,
            high=20,
        )
        for field in (
            "logical_calls_seen",
            "physical_request_start_markers",
            "call_checkpoint_replays",
        ):
            del audit[field]
    elif actual_keys != _AUDIT_KEYS:
        _exact_keys("execution audit", audit, _AUDIT_KEYS)
    if audit != dict(expected):
        raise V3CampaignError("execution audit does not match durable attempt state")
    return audit


def _validate_metrics_against_replay(
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    calls: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
) -> None:
    from .v3_generation import episode_metrics, replay_committed_generation

    try:
        replayed = replay_committed_generation(manifest, entry, calls)
        expected = episode_metrics(replayed, entry)
    except Exception as exc:
        raise V3CampaignError(
            "episode checkpoints cannot deterministically replay"
        ) from exc
    if dict(metrics) != expected:
        raise V3CampaignError(
            "episode metrics disagree with deterministic checkpoint replay"
        )


def validate_episode_seal_payload(
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Purely validate one seal payload against its frozen manifest entry."""

    frozen, manifest_hash = _manifest_payload(manifest)
    detached_entry = _detached(entry, "plan entry")
    shard = detached_entry.get("shard_index")
    if type(shard) is not int or not 0 <= shard < V3_TOTAL_SHARDS:
        raise V3CampaignError("plan entry shard index is invalid")
    if frozen["execution_plan"][shard] != detached_entry:
        raise V3CampaignError("episode seal plan entry is not manifest-bound")
    seal = _detached(payload, "episode seal")
    _exact_keys("episode seal", seal, _SEAL_KEYS)
    if seal["schema_version"] != 1 or seal["kind"] != EPISODE_SEAL_KIND:
        raise V3CampaignError("episode seal contract drifted")
    identity = _transaction_identity(frozen, detached_entry)
    expected_fields = {
        "campaign_manifest_payload_sha256": manifest_hash,
        "execution_plan_sha256": frozen["execution_plan_sha256"],
        "plan_entry_sha256": detached_entry["plan_entry_sha256"],
        "run_id": detached_entry["run_id"],
        "shard_index": shard,
        "phase": detached_entry["phase"],
        "model_stratum": detached_entry["model_stratum"],
        "route_binding_sha256": detached_entry["route_binding_sha256"],
        "transaction_binding_sha256": identity.binding_sha256,
        "logical_calls_completed": 20,
        "private_test_evaluated": False,
    }
    for name, expected in expected_fields.items():
        if seal[name] != expected:
            raise V3CampaignError(f"episode seal {name} drifted from frozen transaction")
    metrics = _validate_episode_metrics(frozen, detached_entry, seal["episode_metrics"])
    if seal["generation_state_sha256"] != metrics["generation_state_sha256"]:
        raise V3CampaignError("episode seal generation-state digest drifted")
    _sha256("generation_state_sha256", seal["generation_state_sha256"])
    call_hashes = seal["ordered_call_checkpoint_payload_sha256"]
    if not isinstance(call_hashes, list) or len(call_hashes) != 20:
        raise V3CampaignError("episode seal must bind exactly 20 call checkpoints")
    for index, digest in enumerate(call_hashes):
        _sha256(f"ordered call hash {index}", digest)
    call_references = seal["ordered_call_checkpoints"]
    if not isinstance(call_references, list) or len(call_references) != 20:
        raise V3CampaignError("episode seal must bind exactly 20 checkpoint files")
    expected_checkpoint_paths = [
        f"{CALL_CHECKPOINT_DIRECTORY_NAME}/shard-{shard:04d}-slot-{slot:02d}.json"
        for slot in range(20)
    ]
    for index, item in enumerate(call_references):
        row = _exact_keys(
            f"call checkpoint reference {index}",
            item,
            _CALL_FILE_REFERENCE_KEYS,
        )
        if row["slot_index"] != index:
            raise V3CampaignError("call checkpoint references are not slot ordered")
        if row["checkpoint_file"] != expected_checkpoint_paths[index]:
            raise V3CampaignError("call checkpoint reference path drifted")
        _sha256("call checkpoint file hash", row["checkpoint_file_sha256"])
        _sha256("call checkpoint payload hash", row["checkpoint_payload_sha256"])
        if row["checkpoint_payload_sha256"] != call_hashes[index]:
            raise V3CampaignError("call checkpoint reference payload hash drifted")
    attempts = seal["ordered_attempt_artifacts"]
    if not isinstance(attempts, list) or not 20 <= len(attempts) <= 60:
        raise V3CampaignError("episode seal attempt inventory has an invalid size")
    previous: tuple[int, int] | None = None
    slots_seen: set[int] = set()
    for index, item in enumerate(attempts):
        row = _exact_keys(f"attempt artifact {index}", item, _ATTEMPT_ARTIFACT_KEYS)
        slot = _integer("attempt artifact slot", row["slot_index"], low=0, high=19)
        ordinal = _integer(
            "attempt artifact ordinal", row["attempt_ordinal"], low=1, high=MAX_PHYSICAL_ATTEMPTS
        )
        coordinate = (slot, ordinal)
        if previous is not None and coordinate <= previous:
            raise V3CampaignError("attempt artifact inventory is not strictly ordered")
        previous = coordinate
        slots_seen.add(slot)
        _sha256("attempt start payload hash", row["start_payload_sha256"])
        if row["outcome_payload_sha256"] is not None:
            _sha256("attempt outcome payload hash", row["outcome_payload_sha256"])
    if slots_seen != set(range(20)):
        raise V3CampaignError("attempt artifact inventory does not cover all slots")
    _exact_keys("execution audit", seal["execution_audit"], _AUDIT_KEYS)
    _sensitive_seal_scan(seal)
    return seal


def build_episode_seal_payload(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    episode_metrics: Mapping[str, Any],
    execution_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one secret-free seal from already durable logical-call state."""

    frozen, manifest_hash = _manifest_payload(manifest)
    detached_entry = _detached(entry, "plan entry")
    shard = detached_entry.get("shard_index")
    if type(shard) is not int or not 0 <= shard < V3_TOTAL_SHARDS or frozen["execution_plan"][shard] != detached_entry:
        raise V3CampaignError("episode plan entry is not manifest-bound")
    metrics = _validate_episode_metrics(frozen, detached_entry, episode_metrics)
    call_hashes, call_references, attempts, calls, expected_audit = _episode_artifacts(
        campaign_dir, frozen, detached_entry
    )
    _validate_metrics_against_replay(frozen, detached_entry, calls, metrics)
    audit = _validate_execution_audit(execution_audit, expected_audit)
    identity = _transaction_identity(frozen, detached_entry)
    payload = {
        "schema_version": 1,
        "kind": EPISODE_SEAL_KIND,
        "campaign_manifest_payload_sha256": manifest_hash,
        "execution_plan_sha256": frozen["execution_plan_sha256"],
        "plan_entry_sha256": detached_entry["plan_entry_sha256"],
        "run_id": detached_entry["run_id"],
        "shard_index": shard,
        "phase": detached_entry["phase"],
        "model_stratum": detached_entry["model_stratum"],
        "route_binding_sha256": detached_entry["route_binding_sha256"],
        "transaction_binding_sha256": identity.binding_sha256,
        "generation_state_sha256": metrics["generation_state_sha256"],
        "logical_calls_completed": 20,
        "ordered_call_checkpoint_payload_sha256": call_hashes,
        "ordered_call_checkpoints": call_references,
        "ordered_attempt_artifacts": attempts,
        "episode_metrics": metrics,
        "execution_audit": audit,
        "private_test_evaluated": False,
    }
    return validate_episode_seal_payload(frozen, detached_entry, payload)


def _load_seal_envelope(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    shard_index: int,
    *,
    verify_artifacts: bool,
) -> dict[str, Any]:
    frozen, _ = _manifest_payload(manifest)
    entry = frozen["execution_plan"][shard_index]
    try:
        envelope = load_envelope(
            episode_seal_path(campaign_dir, shard_index),
            expected_kind=EPISODE_SEAL_KIND,
        )
    except Exception as exc:
        raise V3CampaignError(f"cannot load episode seal {shard_index}") from exc
    seal = validate_episode_seal_payload(frozen, entry, envelope["payload"])
    if verify_artifacts:
        call_hashes, call_references, attempts, calls, expected_audit = _episode_artifacts(
            campaign_dir, frozen, entry
        )
        if call_hashes != seal["ordered_call_checkpoint_payload_sha256"]:
            raise V3CampaignError("episode seal call-checkpoint hashes drifted")
        if call_references != seal["ordered_call_checkpoints"]:
            raise V3CampaignError("episode seal checkpoint-file hashes drifted")
        if attempts != seal["ordered_attempt_artifacts"]:
            raise V3CampaignError("episode seal attempt hashes drifted")
        _validate_metrics_against_replay(
            frozen,
            entry,
            calls,
            seal["episode_metrics"],
        )
        _validate_execution_audit(seal["execution_audit"], expected_audit)
    return envelope


def load_episode_seal(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    shard_index: int,
) -> dict[str, Any]:
    """Load one seal and revalidate every underlying call/attempt hash."""

    _integer("shard_index", shard_index, low=0, high=V3_TOTAL_SHARDS - 1)
    _assert_stored_manifest(campaign_dir, manifest)
    return _load_seal_envelope(
        campaign_dir, manifest, shard_index, verify_artifacts=True
    )


def _seal_indices(campaign_dir: str | Path) -> list[int]:
    directory = Path(campaign_dir) / EPISODE_SEAL_DIRECTORY_NAME
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise V3CampaignError("episode seal directory must be real")
    indices: list[int] = []
    for path in directory.iterdir():
        match = _SEAL_PATTERN.fullmatch(path.name)
        if match is None or path.is_symlink() or not path.is_file():
            raise V3CampaignError("episode seal inventory contains an unknown file")
        index = int(match.group(1))
        if not 0 <= index < V3_TOTAL_SHARDS:
            raise V3CampaignError("episode seal has a future shard coordinate")
        indices.append(index)
    indices.sort()
    if indices != list(range(len(indices))):
        raise V3CampaignError("episode seals are not a contiguous frontier")
    return indices


def _screen_status(campaign_dir: str | Path, manifest: Mapping[str, Any]) -> str | None:
    path = compatibility_screen_path(campaign_dir)
    if not path.exists():
        return None
    return load_compatibility_screen(campaign_dir, manifest)["payload"]["status"]


def _touched_artifact_shards(campaign_dir: str | Path) -> set[int]:
    touched: set[int] = set()
    for directory_name, pattern in (
        (PHYSICAL_ATTEMPT_DIRECTORY_NAME, _ATTEMPT_PATTERN),
        (CALL_CHECKPOINT_DIRECTORY_NAME, _CALL_PATTERN),
    ):
        directory = Path(campaign_dir) / directory_name
        if not directory.exists():
            continue
        for path in directory.iterdir():
            match = pattern.fullmatch(path.name)
            if match is None:
                raise V3CampaignError("logical-call artifact filename drifted")
            touched.add(int(match.group(1)))
    return touched


def next_shard_frontier(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    lease: CampaignLockLease | None = None,
) -> int | None:
    """Return the only shard that may execute, rejecting gaps and future state."""

    if lease is not None:
        _assert_lease(campaign_dir, lease)
    frozen, _ = _assert_stored_manifest(campaign_dir, manifest)
    validate_campaign_inventory(campaign_dir, frozen)
    indices = _seal_indices(campaign_dir)
    # A filename is never progress.  Authenticate the complete committed seal
    # prefix, including all bound call/attempt artifacts, before authorizing a
    # later shard to reach transport.
    for index in indices:
        _load_seal_envelope(
            campaign_dir,
            frozen,
            index,
            verify_artifacts=True,
        )
    frontier = len(indices)
    touched = _touched_artifact_shards(campaign_dir)
    if any(index > frontier for index in touched):
        raise V3CampaignError("call artifacts exist beyond the strict shard frontier")
    screen = _screen_status(campaign_dir, frozen)
    if frontier < V3_GATE_SHARDS and screen is not None:
        raise V3CampaignError("compatibility screen exists before all gate seals")
    if frontier > V3_GATE_SHARDS and screen != "passed":
        raise V3CampaignError("main seals exist without a passing compatibility screen")
    barrier_exists = generation_barrier_path(campaign_dir).exists()
    if frontier < V3_TOTAL_SHARDS and barrier_exists:
        raise V3CampaignError("generation barrier exists before all episode seals")
    if frontier == V3_TOTAL_SHARDS:
        return None
    return frontier


@contextmanager
def acquire_campaign_frontier(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    shard_index: int,
) -> Iterator[CampaignLockLease]:
    """Hold the global lock while executing exactly the current shard frontier."""

    with acquire_campaign_lock(campaign_dir, blocking=False) as lease:
        frontier = next_shard_frontier(campaign_dir, manifest, lease=lease)
        if frontier is None or shard_index != frontier:
            raise V3CampaignError(
                f"requested shard {shard_index} is not the strict frontier {frontier}"
            )
        if shard_index >= V3_GATE_SHARDS:
            status = _screen_status(campaign_dir, manifest)
            if status != "passed":
                raise V3CampaignError("main execution requires a passing compatibility screen")
        yield lease


def publish_episode_seal(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    entry: Mapping[str, Any],
    *,
    episode_metrics: Mapping[str, Any],
    execution_audit: Mapping[str, Any],
    forbidden_values: Sequence[str] = (),
    lease: CampaignLockLease | None = None,
) -> dict[str, Any]:
    """Exclusively commit the current 20-call episode at the global frontier."""

    if lease is None:
        shard = entry.get("shard_index") if isinstance(entry, Mapping) else None
        if type(shard) is not int:
            raise V3CampaignError("episode entry lacks a shard index")
        with acquire_campaign_frontier(campaign_dir, manifest, shard) as owned:
            return publish_episode_seal(
                campaign_dir,
                manifest,
                entry,
                episode_metrics=episode_metrics,
                execution_audit=execution_audit,
                forbidden_values=forbidden_values,
                lease=owned,
            )
    _assert_lease(campaign_dir, lease)
    frozen, _ = _assert_stored_manifest(campaign_dir, manifest)
    shard = entry.get("shard_index") if isinstance(entry, Mapping) else None
    frontier = next_shard_frontier(campaign_dir, frozen, lease=lease)
    if type(shard) is not int or shard != frontier:
        raise V3CampaignError("episode seal is not at the strict shard frontier")
    if shard >= V3_GATE_SHARDS and _screen_status(campaign_dir, frozen) != "passed":
        raise V3CampaignError("main episode seal requires a passing compatibility screen")
    payload = build_episode_seal_payload(
        campaign_dir,
        frozen,
        entry,
        episode_metrics=episode_metrics,
        execution_audit=execution_audit,
    )
    return publish_envelope(
        episode_seal_path(campaign_dir, shard),
        kind=EPISODE_SEAL_KIND,
        payload=payload,
        forbidden_values=forbidden_values,
    )


def _build_compatibility_screen_payload(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    gate_envelopes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    frozen, manifest_hash = _manifest_payload(manifest)
    indices = _seal_indices(campaign_dir)
    if len(indices) < V3_GATE_SHARDS:
        raise V3CampaignError("compatibility screen requires all eight gate seals")
    if gate_envelopes is None:
        gate_envelopes = [
            _load_seal_envelope(campaign_dir, frozen, index, verify_artifacts=True)
            for index in range(V3_GATE_SHARDS)
        ]
    elif len(gate_envelopes) != V3_GATE_SHARDS:
        raise V3CampaignError("compatibility screen requires exactly eight gate envelopes")
    routes: list[dict[str, Any]] = []
    for model_stratum in V3_MODEL_STRATA:
        arm_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        for arm_id in V3_ARM_IDS:
            matching = [
                envelope["payload"]
                for envelope in gate_envelopes
                if envelope["payload"]["model_stratum"] == model_stratum
                and frozen["execution_plan"][envelope["payload"]["shard_index"]]["arm_id"]
                == arm_id
            ]
            if len(matching) != 1:
                raise V3CampaignError("gate seals do not cover each route-arm cell exactly once")
            metrics = matching[0]["episode_metrics"]
            valid = metrics["search_valid_count"]
            arm_passed = valid >= 18
            if not arm_passed:
                failures.append(f"arm_{arm_id}_search_valid_below_18")
            arm_rows.append(
                {
                    "arm_id": arm_id,
                    "planned_call_count": 20,
                    "search_valid_count": valid,
                    "canonical_unique_count": metrics["canonical_unique_count"],
                    "behavioral_unique_count": metrics["behavioral_unique_count"],
                    "validity_passed": arm_passed,
                }
            )
        overall = sum(row["search_valid_count"] for row in arm_rows)
        if overall < 76:
            failures.insert(0, "overall_search_valid_below_76")
        by_arm = {row["arm_id"]: row for row in arm_rows}
        canonical_delta = (
            by_arm["H"]["canonical_unique_count"]
            - by_arm["L"]["canonical_unique_count"]
        )
        behavioral_delta = (
            by_arm["H"]["behavioral_unique_count"]
            - by_arm["L"]["behavioral_unique_count"]
        )
        if canonical_delta <= 0:
            failures.append("H_not_greater_than_L_canonical_unique")
        if behavioral_delta <= 0:
            failures.append("H_not_greater_than_L_behavioral_unique")
        routes.append(
            {
                "model_stratum": model_stratum,
                "planned_call_count": 80,
                "search_valid_count": overall,
                "minimum_overall_search_valid_count": 76,
                "minimum_per_arm_search_valid_count": 18,
                "arms": arm_rows,
                "h_minus_l_canonical_unique_count": canonical_delta,
                "h_minus_l_behavioral_unique_count": behavioral_delta,
                "passed": not failures,
                "failure_codes": failures,
            }
        )
    passed = all(route["passed"] for route in routes)
    return {
        "schema_version": 1,
        "kind": COMPATIBILITY_SCREEN_KIND,
        "campaign_manifest_payload_sha256": manifest_hash,
        "execution_plan_sha256": frozen["execution_plan_sha256"],
        "ordered_gate_seal_payload_sha256": [
            envelope["payload_sha256"] for envelope in gate_envelopes
        ],
        "gate_shard_count": 8,
        "gate_logical_calls_completed": 160,
        "routes": routes,
        "both_routes_passed": passed,
        "status": "passed" if passed else "compatibility_screen_failed",
        "private_test_evaluated": False,
    }


def _validate_screen_shape(payload: Mapping[str, Any]) -> None:
    _exact_keys("compatibility screen", payload, _SCREEN_KEYS)
    if payload["schema_version"] != 1 or payload["kind"] != COMPATIBILITY_SCREEN_KIND:
        raise V3CampaignError("compatibility screen contract drifted")
    _sha256("screen manifest hash", payload["campaign_manifest_payload_sha256"])
    _sha256("screen plan hash", payload["execution_plan_sha256"])
    hashes = payload["ordered_gate_seal_payload_sha256"]
    if not isinstance(hashes, list) or len(hashes) != 8:
        raise V3CampaignError("compatibility screen must bind eight seals")
    for digest in hashes:
        _sha256("gate seal payload hash", digest)
    if payload["gate_shard_count"] != 8 or payload["gate_logical_calls_completed"] != 160:
        raise V3CampaignError("compatibility screen gate budget drifted")
    routes = payload["routes"]
    if not isinstance(routes, list) or len(routes) != 2:
        raise V3CampaignError("compatibility screen must contain two routes")
    if [route.get("model_stratum") for route in routes if isinstance(route, Mapping)] != list(V3_MODEL_STRATA):
        raise V3CampaignError("compatibility screen route ordering drifted")
    for route in routes:
        _exact_keys("compatibility route", route, _ROUTE_SCREEN_KEYS)
        if not isinstance(route["arms"], list) or len(route["arms"]) != 4:
            raise V3CampaignError("compatibility route must contain four arms")
        for arm in route["arms"]:
            _exact_keys("compatibility arm", arm, _ARM_SCREEN_KEYS)
        if not isinstance(route["failure_codes"], list) or any(
            not isinstance(item, str) for item in route["failure_codes"]
        ):
            raise V3CampaignError("compatibility failure codes are invalid")
    if payload["private_test_evaluated"] is not False:
        raise V3CampaignError("compatibility screen cannot contain private-test results")


def publish_compatibility_screen(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    lease: CampaignLockLease | None = None,
) -> dict[str, Any]:
    """Publish the pass/fail screen after exactly eight gate episode seals."""

    if lease is None:
        with acquire_campaign_lock(campaign_dir) as owned:
            return publish_compatibility_screen(campaign_dir, manifest, lease=owned)
    _assert_lease(campaign_dir, lease)
    frozen, _ = _assert_stored_manifest(campaign_dir, manifest)
    validate_campaign_inventory(campaign_dir, frozen)
    if _seal_indices(campaign_dir) != list(range(V3_GATE_SHARDS)):
        raise V3CampaignError("screen publication requires exactly eight gate seals and no main seal")
    if compatibility_screen_path(campaign_dir).exists():
        raise V3CampaignError("compatibility screen is already immutable")
    payload = _build_compatibility_screen_payload(campaign_dir, frozen)
    _validate_screen_shape(payload)
    return publish_envelope(
        compatibility_screen_path(campaign_dir),
        kind=COMPATIBILITY_SCREEN_KIND,
        payload=payload,
    )


def load_compatibility_screen(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Load and recompute the gate screen from the exact eight gate seals."""

    frozen, _ = _assert_stored_manifest(campaign_dir, manifest)
    try:
        envelope = load_envelope(
            compatibility_screen_path(campaign_dir),
            expected_kind=COMPATIBILITY_SCREEN_KIND,
        )
    except Exception as exc:
        raise V3CampaignError("cannot load compatibility screen") from exc
    _validate_screen_shape(envelope["payload"])
    expected = _build_compatibility_screen_payload(campaign_dir, frozen)
    if envelope["payload"] != expected:
        raise V3CampaignError("compatibility screen differs from its gate seals")
    return envelope


def _validate_regular_mode_0600(path: Path, name: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise V3CampaignError(f"cannot inspect {name}") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode):
        raise V3CampaignError(f"{name} must be a regular file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise V3CampaignError(f"{name} must have mode 0600")


def validate_campaign_inventory(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    deep_logical_artifacts: bool = True,
) -> None:
    """Reject unknown, future, symlinked, or malformed campaign artifacts."""

    root = _root(campaign_dir)
    frozen, _ = _manifest_payload(manifest)
    allowed_root_files = {
        CAMPAIGN_MANIFEST_NAME,
        COMPATIBILITY_SCREEN_NAME,
        GENERATION_BARRIER_NAME,
        FINALIZED_SNAPSHOT_NAME,
    }
    allowed_directories = {
        EPISODE_SEAL_DIRECTORY_NAME,
        CAMPAIGN_LOCK_DIRECTORY_NAME,
        PHYSICAL_ATTEMPT_DIRECTORY_NAME,
        CALL_CHECKPOINT_DIRECTORY_NAME,
        SHARD_LOCK_DIRECTORY_NAME,
    }
    for path in root.iterdir():
        if path.is_symlink():
            raise V3CampaignError("campaign inventory refuses symlinks")
        if path.is_file():
            if path.name not in allowed_root_files:
                raise V3CampaignError("campaign inventory contains an unknown root file")
            _validate_regular_mode_0600(path, path.name)
        elif path.is_dir():
            if path.name not in allowed_directories:
                raise V3CampaignError("campaign inventory contains an unknown directory")
        else:
            raise V3CampaignError("campaign inventory contains a non-file entry")
    _assert_stored_manifest(root, frozen)
    if type(deep_logical_artifacts) is not bool:
        raise TypeError("deep_logical_artifacts must be a boolean")
    if deep_logical_artifacts:
        validate_artifact_inventory(root)
    else:
        for directory_name, pattern in (
            (PHYSICAL_ATTEMPT_DIRECTORY_NAME, _ATTEMPT_PATTERN),
            (CALL_CHECKPOINT_DIRECTORY_NAME, _CALL_PATTERN),
        ):
            directory = root / directory_name
            if not directory.exists():
                continue
            if directory.is_symlink() or not directory.is_dir():
                raise V3CampaignError("logical-call artifact directory must be real")
            for path in directory.iterdir():
                match = pattern.fullmatch(path.name)
                if match is None:
                    raise V3CampaignError("logical-call artifact inventory contains an unknown file")
                shard = int(match.group(1))
                slot = int(match.group(2))
                if not (0 <= shard < V3_TOTAL_SHARDS and 0 <= slot < SLOTS_PER_SHARD):
                    raise V3CampaignError("logical-call artifact has a future coordinate")
                if pattern is _ATTEMPT_PATTERN:
                    ordinal = int(match.group(3))
                    if not 1 <= ordinal <= MAX_PHYSICAL_ATTEMPTS:
                        raise V3CampaignError("physical attempt artifact ordinal drifted")
                _validate_regular_mode_0600(path, "logical-call artifact")
    _seal_indices(root)
    lock_directory = root / CAMPAIGN_LOCK_DIRECTORY_NAME
    if lock_directory.exists():
        for path in lock_directory.iterdir():
            if path.name != CAMPAIGN_LOCK_NAME:
                raise V3CampaignError("campaign lock inventory contains an unknown file")
            _validate_regular_mode_0600(path, "campaign lock")
    shard_locks = root / SHARD_LOCK_DIRECTORY_NAME
    if shard_locks.exists():
        for path in shard_locks.iterdir():
            match = _SHARD_LOCK_PATTERN.fullmatch(path.name)
            if match is None or int(match.group(1)) >= V3_TOTAL_SHARDS:
                raise V3CampaignError("shard lock inventory contains an unknown file")
            _validate_regular_mode_0600(path, "shard lock")


def _artifact_inventory(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    screen: Mapping[str, Any] | None = None,
    seals: Sequence[Mapping[str, Any]] | None = None,
    inventory_validated: bool = False,
) -> list[dict[str, Any]]:
    root = _root(campaign_dir)
    frozen, _ = _manifest_payload(manifest)
    if not inventory_validated:
        validate_campaign_inventory(root, frozen)
    inventory: list[dict[str, Any]] = []

    def append(
        path: Path,
        kind: str,
        payload_sha256: str,
        *,
        file_sha256: str | None = None,
    ) -> None:
        inventory.append(
            {
                "path": path.relative_to(root).as_posix(),
                "kind": kind,
                "file_sha256": (
                    sha256_bytes(path.read_bytes())
                    if file_sha256 is None
                    else file_sha256
                ),
                "payload_sha256": payload_sha256,
            }
        )

    stored_manifest = load_envelope(
        campaign_manifest_path(root), expected_kind=V3_CAMPAIGN_MANIFEST_KIND
    )
    append(
        campaign_manifest_path(root),
        V3_CAMPAIGN_MANIFEST_KIND,
        stored_manifest["payload_sha256"],
    )
    if screen is None:
        screen = load_compatibility_screen(root, frozen)
    append(
        compatibility_screen_path(root),
        COMPATIBILITY_SCREEN_KIND,
        screen["payload_sha256"],
    )
    if seals is None:
        seals = [
            _load_seal_envelope(root, frozen, shard, verify_artifacts=True)
            for shard in range(V3_TOTAL_SHARDS)
        ]
    if len(seals) != V3_TOTAL_SHARDS:
        raise V3CampaignError("artifact inventory requires all 104 episode seals")
    for shard, seal in enumerate(seals):
        payload = seal["payload"]
        if payload["shard_index"] != shard:
            raise V3CampaignError("prevalidated seal ordering drifted")
        append(
            episode_seal_path(root, shard),
            EPISODE_SEAL_KIND,
            seal["payload_sha256"],
        )
        for row in payload["ordered_attempt_artifacts"]:
            slot = row["slot_index"]
            ordinal = row["attempt_ordinal"]
            append(
                attempt_start_path(root, shard, slot, ordinal),
                "v3-physical-attempt-start",
                row["start_payload_sha256"],
            )
            if row["outcome_payload_sha256"] is not None:
                append(
                    attempt_outcome_path(root, shard, slot, ordinal),
                    "v3-physical-attempt-outcome",
                    row["outcome_payload_sha256"],
                )
        for reference in payload["ordered_call_checkpoints"]:
            append(
                root / reference["checkpoint_file"],
                "v3-logical-call-checkpoint",
                reference["checkpoint_payload_sha256"],
                file_sha256=reference["checkpoint_file_sha256"],
            )
    inventory.sort(key=lambda item: item["path"])
    return inventory


def _build_generation_barrier_payload(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    frozen, manifest_hash = _manifest_payload(manifest)
    if _seal_indices(campaign_dir) != list(range(V3_TOTAL_SHARDS)):
        raise V3CampaignError("generation barrier requires exactly 104 contiguous seals")
    validate_campaign_inventory(
        campaign_dir,
        frozen,
        deep_logical_artifacts=False,
    )
    seals = [
        _load_seal_envelope(campaign_dir, frozen, index, verify_artifacts=True)
        for index in range(V3_TOTAL_SHARDS)
    ]
    try:
        screen = load_envelope(
            compatibility_screen_path(campaign_dir),
            expected_kind=COMPATIBILITY_SCREEN_KIND,
        )
    except Exception as exc:
        raise V3CampaignError("cannot load compatibility screen") from exc
    _validate_screen_shape(screen["payload"])
    expected_screen = _build_compatibility_screen_payload(
        campaign_dir,
        frozen,
        gate_envelopes=seals[:V3_GATE_SHARDS],
    )
    if screen["payload"] != expected_screen:
        raise V3CampaignError("compatibility screen differs from its gate seals")
    if screen["payload"]["status"] != "passed":
        raise V3CampaignError("a failed compatibility screen forbids a generation barrier")
    phases = [envelope["payload"]["phase"] for envelope in seals]
    if phases[:V3_GATE_SHARDS] != ["gate"] * V3_GATE_SHARDS or phases[V3_GATE_SHARDS:] != ["main"] * V3_MAIN_SHARDS:
        raise V3CampaignError("episode seal phase partition drifted")
    main_calls = sum(
        envelope["payload"]["logical_calls_completed"]
        for envelope in seals[V3_GATE_SHARDS:]
    )
    if main_calls != 1920:
        raise V3CampaignError("main generation budget is not exactly 1,920 calls")
    inventory = _artifact_inventory(
        campaign_dir,
        frozen,
        screen=screen,
        seals=seals,
        inventory_validated=True,
    )
    return {
        "schema_version": 1,
        "kind": GENERATION_BARRIER_KIND,
        "campaign_manifest_payload_sha256": manifest_hash,
        "execution_plan_sha256": frozen["execution_plan_sha256"],
        "compatibility_screen_payload_sha256": screen["payload_sha256"],
        "ordered_episode_seal_payload_sha256": [
            envelope["payload_sha256"] for envelope in seals
        ],
        "gate_shard_count": 8,
        "main_shard_count": 96,
        "main_logical_calls_completed": main_calls,
        "total_logical_calls_completed": sum(
            envelope["payload"]["logical_calls_completed"] for envelope in seals
        ),
        "artifact_inventory": inventory,
        "artifact_inventory_sha256": sha256_json(inventory),
        "generation_complete": True,
        "private_test_evaluated": False,
    }


def _validate_barrier_shape(payload: Mapping[str, Any]) -> None:
    _exact_keys("generation barrier", payload, _BARRIER_KEYS)
    if payload["schema_version"] != 1 or payload["kind"] != GENERATION_BARRIER_KIND:
        raise V3CampaignError("generation barrier contract drifted")
    for name in (
        "campaign_manifest_payload_sha256",
        "execution_plan_sha256",
        "compatibility_screen_payload_sha256",
        "artifact_inventory_sha256",
    ):
        _sha256(name, payload[name])
    seal_hashes = payload["ordered_episode_seal_payload_sha256"]
    if not isinstance(seal_hashes, list) or len(seal_hashes) != 104:
        raise V3CampaignError("generation barrier must bind 104 seals")
    for digest in seal_hashes:
        _sha256("episode seal payload hash", digest)
    inventory = payload["artifact_inventory"]
    if not isinstance(inventory, list) or not inventory:
        raise V3CampaignError("generation barrier artifact inventory is empty")
    paths: list[str] = []
    for item in inventory:
        row = _exact_keys("artifact inventory item", item, _INVENTORY_ITEM_KEYS)
        path = row["path"]
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise V3CampaignError("artifact inventory path is not normalized relative")
        if not isinstance(row["kind"], str) or not row["kind"]:
            raise V3CampaignError("artifact inventory kind is invalid")
        _sha256("artifact file hash", row["file_sha256"])
        _sha256("artifact payload hash", row["payload_sha256"])
        paths.append(path)
    if paths != sorted(set(paths)):
        raise V3CampaignError("artifact inventory paths are not sorted and unique")
    if sha256_json(inventory) != payload["artifact_inventory_sha256"]:
        raise V3CampaignError("artifact inventory hash drifted")
    if (
        payload["gate_shard_count"] != 8
        or payload["main_shard_count"] != 96
        or payload["main_logical_calls_completed"] != 1920
        or payload["total_logical_calls_completed"] != 2080
        or payload["generation_complete"] is not True
        or payload["private_test_evaluated"] is not False
    ):
        raise V3CampaignError("generation barrier completion contract drifted")


def publish_generation_barrier(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
    *,
    lease: CampaignLockLease | None = None,
) -> dict[str, Any]:
    """Publish the generation-only barrier after all 104 episode seals."""

    if lease is None:
        with acquire_campaign_lock(campaign_dir) as owned:
            return publish_generation_barrier(campaign_dir, manifest, lease=owned)
    _assert_lease(campaign_dir, lease)
    frozen, _ = _assert_stored_manifest(campaign_dir, manifest)
    if generation_barrier_path(campaign_dir).exists():
        raise V3CampaignError("generation barrier is already immutable")
    payload = _build_generation_barrier_payload(campaign_dir, frozen)
    _validate_barrier_shape(payload)
    return publish_envelope(
        generation_barrier_path(campaign_dir),
        kind=GENERATION_BARRIER_KIND,
        payload=payload,
    )


def load_generation_barrier(
    campaign_dir: str | Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Load the barrier and re-enumerate all 104 seals and all call artifacts."""

    frozen, _ = _assert_stored_manifest(campaign_dir, manifest)
    try:
        envelope = load_envelope(
            generation_barrier_path(campaign_dir),
            expected_kind=GENERATION_BARRIER_KIND,
        )
    except Exception as exc:
        raise V3CampaignError("cannot load generation barrier") from exc
    _validate_barrier_shape(envelope["payload"])
    expected = _build_generation_barrier_payload(campaign_dir, frozen)
    if envelope["payload"] != expected:
        raise V3CampaignError("generation barrier differs from current immutable artifacts")
    return envelope


__all__ = [
    "CAMPAIGN_LOCK_DIRECTORY_NAME",
    "CAMPAIGN_LOCK_NAME",
    "CAMPAIGN_MANIFEST_NAME",
    "COMPATIBILITY_SCREEN_KIND",
    "COMPATIBILITY_SCREEN_NAME",
    "CampaignLockLease",
    "EPISODE_SEAL_DIRECTORY_NAME",
    "EPISODE_SEAL_KIND",
    "GENERATION_BARRIER_KIND",
    "GENERATION_BARRIER_NAME",
    "V3CampaignError",
    "acquire_campaign_frontier",
    "acquire_campaign_lock",
    "build_episode_seal_payload",
    "campaign_lock_path",
    "campaign_manifest_path",
    "compatibility_screen_path",
    "episode_seal_path",
    "generation_barrier_path",
    "load_campaign_manifest",
    "load_compatibility_screen",
    "load_episode_seal",
    "load_generation_barrier",
    "next_shard_frontier",
    "publish_campaign_manifest",
    "publish_compatibility_screen",
    "publish_episode_seal",
    "publish_generation_barrier",
    "validate_campaign_inventory",
    "validate_episode_seal_payload",
]
