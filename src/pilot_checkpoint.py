"""Tamper-evident atomic storage for the staged development pilot.

Checkpoint files are the sole commit authority for paid scientific shards.
Attempt ledgers are operational audit trails only: they can explain discarded
or ambiguous cost, but can never make an uncommitted shard scientific data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
from typing import Any

from .dsl import parse_sexpr, to_sexpr


CHECKPOINT_SCHEMA_VERSION = 1
INVALID_CANDIDATE_SENTINEL = "__INVALID_CANDIDATE_EXPRESSION__"
CAMPAIGN_MANIFEST_NAME = "campaign-manifest.json"
CHECKPOINT_DIRECTORY_NAME = "checkpoints"
WORLD_SEAL_DIRECTORY_NAME = "world-seals"
ATTEMPT_DIRECTORY_NAME = "attempts"
SNAPSHOT_DIRECTORY_NAME = "snapshots"

_FORBIDDEN_CHECKPOINT_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "content",
        "endpoint",
        "expression",
        "final_test",
        "private_test",
        "private_test_label",
        "private_test_labels",
        "prompt",
        "raw",
        "raw_content",
        "raw_endpoint",
        "raw_prompt",
        "raw_response",
        "test_examples",
        "test_label",
        "test_labels",
    }
)
_FORBIDDEN_PUBLIC_KEYS = frozenset(
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
        "raw_endpoint",
        "raw_prompt",
        "raw_response",
        "test_examples",
        "test_label",
        "test_labels",
    }
)


class PilotCheckpointError(RuntimeError):
    """Raised when staged-pilot durable state is unsafe or inconsistent."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PilotCheckpointError("checkpoint data must be finite JSON") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _reject_sensitive_checkpoint_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_CHECKPOINT_KEYS:
                raise PilotCheckpointError(
                    f"checkpoint contains forbidden private/raw field: {normalized}"
                )
            _reject_sensitive_checkpoint_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive_checkpoint_fields(item)


def _reject_sensitive_public_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_PUBLIC_KEYS:
                raise PilotCheckpointError(
                    f"public snapshot contains forbidden private/raw field: {normalized}"
                )
            _reject_sensitive_public_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_sensitive_public_fields(item)


def _validate_canonical_candidate_expressions(payload: Mapping[str, Any]) -> None:
    run = payload.get("run")
    if not isinstance(run, Mapping):
        return
    candidates = run.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise PilotCheckpointError("checkpoint run candidates must be an array")
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise PilotCheckpointError("checkpoint candidate must be an object")
        expression = candidate.get("candidate_expression")
        if expression == INVALID_CANDIDATE_SENTINEL:
            if candidate.get("syntax_valid") is not False:
                raise PilotCheckpointError(
                    "invalid candidate sentinel requires syntax_valid=false"
                )
            continue
        if not isinstance(expression, str) or not expression:
            raise PilotCheckpointError(
                f"checkpoint candidate {index} lacks a canonical DSL expression"
            )
        try:
            canonical = to_sexpr(parse_sexpr(expression))
        except Exception as exc:
            raise PilotCheckpointError(
                f"checkpoint candidate {index} is not valid frozen DSL"
            ) from exc
        if canonical != expression or candidate.get("syntax_valid") is not True:
            raise PilotCheckpointError(
                f"checkpoint candidate {index} expression is not canonical"
            )


def _validate_secret_free(
    value: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
    checkpoint_payload: bool = False,
) -> bytes:
    _reject_sensitive_checkpoint_fields(value)
    if checkpoint_payload:
        _validate_canonical_candidate_expressions(value)
    encoded = canonical_json_bytes(value)
    text = encoded.decode("utf-8")
    if any(secret and secret in text for secret in forbidden_values):
        raise PilotCheckpointError("refusing to persist a secret or raw endpoint")
    return encoded


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_publish_json(
    path: str | Path,
    value: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
    checkpoint_payload: bool = False,
) -> None:
    """Publish one immutable mode-0600 JSON file without overwrite races."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(destination.parent, 0o700)
    except OSError:
        pass
    encoded = _validate_secret_free(
        value,
        forbidden_values=forbidden_values,
        checkpoint_payload=checkpoint_payload,
    ) + b"\n"
    temporary = destination.parent / (
        f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor: int | None = None
    linked = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
        # A hard link is an atomic exclusive publication: unlike replace(), it
        # cannot silently overwrite an existing commit.
        os.link(temporary, destination)
        linked = True
        os.chmod(destination, 0o600)
        _fsync_directory(destination.parent)
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite immutable file: {destination}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        finally:
            if linked:
                _fsync_directory(destination.parent)


def atomic_publish_public_snapshot(
    path: str | Path,
    value: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> None:
    """Atomically publish a result that may contain evaluated test scores."""

    _reject_sensitive_public_fields(value)
    encoded = canonical_json_bytes(value).decode("utf-8")
    if any(secret and secret in encoded for secret in forbidden_values):
        raise PilotCheckpointError("refusing to persist a secret or raw endpoint")
    # Reuse the exclusive publication primitive after public-specific checks.
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.parent / (
        f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor: int | None = None
    linked = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(encoded.encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o600)
        os.link(temporary, destination)
        linked = True
        os.chmod(destination, 0o600)
        _fsync_directory(destination.parent)
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite immutable file: {destination}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        finally:
            if linked:
                _fsync_directory(destination.parent)


def _envelope(kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    detached = json.loads(canonical_json_bytes(payload).decode("utf-8"))
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "kind": kind,
        "payload_sha256": sha256_json(detached),
        "payload": detached,
    }


def publish_envelope(
    path: str | Path,
    *,
    kind: str,
    payload: Mapping[str, Any],
    forbidden_values: Sequence[str] = (),
    checkpoint_payload: bool = False,
) -> dict[str, Any]:
    envelope = _envelope(kind, payload)
    if checkpoint_payload:
        _validate_canonical_candidate_expressions(envelope["payload"])
    atomic_publish_json(
        path,
        envelope,
        forbidden_values=forbidden_values,
        checkpoint_payload=False,
    )
    return envelope


def load_envelope(
    path: str | Path,
    *,
    expected_kind: str,
    checkpoint_payload: bool = False,
) -> dict[str, Any]:
    source = Path(path)
    try:
        file_stat = source.lstat()
    except OSError as exc:
        raise PilotCheckpointError(f"cannot read immutable file: {source}") from exc
    if not stat.S_ISREG(file_stat.st_mode) or source.is_symlink():
        raise PilotCheckpointError("immutable campaign state must be a regular file")
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise PilotCheckpointError("immutable campaign state must have mode 0600")
    try:
        raw = source.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotCheckpointError("immutable campaign state is not UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise PilotCheckpointError("immutable campaign envelope must be an object")
    expected_envelope_keys = {
        "schema_version",
        "kind",
        "payload_sha256",
        "payload",
    }
    if set(value) != expected_envelope_keys:
        raise PilotCheckpointError("immutable campaign envelope keys drifted")
    # Scan the complete envelope, not only its hashed payload.  Otherwise an
    # attacker could append an unhashed top-level raw/private field while
    # keeping payload_sha256 valid.
    _reject_sensitive_checkpoint_fields(value)
    if value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise PilotCheckpointError("immutable campaign schema version drifted")
    if value.get("kind") != expected_kind:
        if value.get("kind") in {"attempt-ledger", "legacy-pilot-attempt"}:
            raise PilotCheckpointError(
                "legacy/attempt ledgers are operational audit only and cannot be imported"
            )
        raise PilotCheckpointError("immutable campaign artifact kind drifted")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise PilotCheckpointError("immutable campaign payload must be an object")
    if not _is_sha256(value.get("payload_sha256")) or value[
        "payload_sha256"
    ] != sha256_json(payload):
        raise PilotCheckpointError("immutable campaign payload hash mismatch")
    _reject_sensitive_checkpoint_fields(payload)
    if checkpoint_payload:
        _validate_canonical_candidate_expressions(payload)
    return dict(value)


def checkpoint_path(campaign_dir: str | Path, shard_index: int) -> Path:
    if type(shard_index) is not int or not 0 <= shard_index < 56:
        raise PilotCheckpointError("shard index must be in [0, 55]")
    return Path(campaign_dir) / CHECKPOINT_DIRECTORY_NAME / f"shard-{shard_index:02d}.json"


def world_seal_path(campaign_dir: str | Path, world_index: int) -> Path:
    if type(world_index) is not int or not 0 <= world_index < 8:
        raise PilotCheckpointError("world index must be in [0, 7]")
    return Path(campaign_dir) / WORLD_SEAL_DIRECTORY_NAME / f"world-{world_index:02d}.json"


def attempt_ledger_path(
    campaign_dir: str | Path,
    shard_index: int,
    attempt_number: int,
) -> Path:
    if type(attempt_number) is not int or not 1 <= attempt_number <= 3:
        raise PilotCheckpointError("attempt number must be in [1, 3]")
    return (
        Path(campaign_dir)
        / ATTEMPT_DIRECTORY_NAME
        / f"shard-{shard_index:02d}-attempt-{attempt_number:02d}.jsonl"
    )


def publish_campaign_manifest(
    campaign_dir: str | Path,
    payload: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    path = Path(campaign_dir) / CAMPAIGN_MANIFEST_NAME
    return publish_envelope(
        path,
        kind="staged-pilot-campaign-manifest",
        payload=payload,
        forbidden_values=forbidden_values,
    )


def load_campaign_manifest(campaign_dir: str | Path) -> dict[str, Any]:
    return load_envelope(
        Path(campaign_dir) / CAMPAIGN_MANIFEST_NAME,
        expected_kind="staged-pilot-campaign-manifest",
    )


def publish_shard_checkpoint(
    campaign_dir: str | Path,
    payload: Mapping[str, Any],
    *,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    shard_index = payload.get("shard_index")
    if type(shard_index) is not int:
        raise PilotCheckpointError("checkpoint shard_index must be an integer")
    _validate_canonical_candidate_expressions(payload)
    return publish_envelope(
        checkpoint_path(campaign_dir, shard_index),
        kind="staged-pilot-shard-checkpoint",
        payload=payload,
        forbidden_values=forbidden_values,
        checkpoint_payload=True,
    )


def load_shard_checkpoint(
    campaign_dir: str | Path,
    shard_index: int,
) -> dict[str, Any]:
    return load_envelope(
        checkpoint_path(campaign_dir, shard_index),
        expected_kind="staged-pilot-shard-checkpoint",
        checkpoint_payload=True,
    )


def publish_world_seal(
    campaign_dir: str | Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    world_index = payload.get("world_index")
    if type(world_index) is not int:
        raise PilotCheckpointError("world seal index must be an integer")
    return publish_envelope(
        world_seal_path(campaign_dir, world_index),
        kind="staged-pilot-world-seal",
        payload=payload,
    )


def load_world_seal(campaign_dir: str | Path, world_index: int) -> dict[str, Any]:
    return load_envelope(
        world_seal_path(campaign_dir, world_index),
        expected_kind="staged-pilot-world-seal",
    )


__all__ = [
    "ATTEMPT_DIRECTORY_NAME",
    "CAMPAIGN_MANIFEST_NAME",
    "CHECKPOINT_DIRECTORY_NAME",
    "CHECKPOINT_SCHEMA_VERSION",
    "INVALID_CANDIDATE_SENTINEL",
    "PilotCheckpointError",
    "SNAPSHOT_DIRECTORY_NAME",
    "WORLD_SEAL_DIRECTORY_NAME",
    "atomic_publish_json",
    "atomic_publish_public_snapshot",
    "attempt_ledger_path",
    "canonical_json_bytes",
    "checkpoint_path",
    "load_campaign_manifest",
    "load_envelope",
    "load_shard_checkpoint",
    "load_world_seal",
    "publish_campaign_manifest",
    "publish_envelope",
    "publish_shard_checkpoint",
    "publish_world_seal",
    "sha256_bytes",
    "sha256_json",
    "world_seal_path",
]
