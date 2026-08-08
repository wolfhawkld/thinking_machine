"""Crash-safe staged execution and offline finalization for the pilot.

The paid process commits one complete 20-call world/arm episode at a time.  It
never evaluates private tests and never reads public snapshot artifacts.  A
separate credential-free finalizer verifies and replays committed checkpoints
before releasing private-test results at the frozen 2/4/8-world boundaries.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
from types import MappingProxyType
from typing import Any, TextIO
import uuid

from .credentials import ProviderCredentials, load_provider_credentials
from .development_pilot import (
    DEVELOPMENT_PILOT_ARMS,
    DEVELOPMENT_PILOT_EPISODE,
    DEVELOPMENT_PILOT_EXPECTED_CALLS,
    DEVELOPMENT_PILOT_EXPECTED_RUNS,
    DEVELOPMENT_PILOT_MODE,
    DEVELOPMENT_PILOT_MODEL,
    DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
    DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER,
    DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL,
    DEVELOPMENT_PILOT_WORLDS,
    _config_sha256,
    _normalize_endpoint_url,
    _public_provider_contract,
    preflight_development_pilot,
    validate_development_pilot_config,
)
from .experiment import (
    SAMPLING_BASE_SEED,
    GeneratorContext,
    _aggregate_budget,
    _arm_execution_order,
    _call_generator_factory,
    _hash,
    _policy_from_config,
    _run_summary,
)
from .pilot_checkpoint import (
    ATTEMPT_DIRECTORY_NAME,
    CAMPAIGN_MANIFEST_NAME,
    INVALID_CANDIDATE_SENTINEL,
    PilotCheckpointError,
    atomic_publish_public_snapshot,
    attempt_ledger_path,
    canonical_json_bytes,
    checkpoint_path,
    load_campaign_manifest,
    load_shard_checkpoint,
    load_world_seal,
    publish_campaign_manifest,
    publish_shard_checkpoint,
    publish_world_seal,
    sha256_bytes,
    sha256_json,
    world_seal_path,
)
from .providers import OpenAICompatibleGeneratorFactory, TransportError
from .provenance import PROJECT_ROOT, source_manifest
from .runner import (
    CANDIDATE_FORMATS,
    GenerationResponse,
    _stable_hash,
    evaluate_episode_test,
    run_episode,
)
from .verifier import Verifier
from .world_generator import generate_world


STAGED_PILOT_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "pilot-stages-volcengine.json"
)
STAGED_PILOT_MODE = "staged-development-pilot-live"
OFFLINE_FINALIZED_MODE = "staged-development-pilot-offline-finalized"
SHARDS_PER_WORLD = 7
CALLS_PER_SHARD = 20
MAX_ATTEMPTS_PER_SHARD = 3
DECISION_BOUNDARIES = (2, 4, 8)
CHECKPOINT_BOUNDARIES = tuple(value * SHARDS_PER_WORLD for value in DECISION_BOUNDARIES)
_CHECKPOINT_RE = re.compile(r"shard-(\d{2})\.json\Z")
_ATTEMPT_RE = re.compile(r"shard-(\d{2})-attempt-(\d{2})\.jsonl\Z")

_EXPECTED_STAGING = {
    "transaction_unit": "world-arm-episode-20-calls",
    "max_attempts_per_shard": MAX_ATTEMPTS_PER_SHARD,
    "world_seal_shards": SHARDS_PER_WORLD,
    "decision_boundaries_world_count": list(DECISION_BOUNDARIES),
    "stages": [
        {"stage_id": "S1", "new_world_indices": [0, 1], "cumulative_world_count": 2},
        {"stage_id": "S2", "new_world_indices": [2, 3], "cumulative_world_count": 4},
        {
            "stage_id": "S3",
            "new_world_indices": [4, 5, 6, 7],
            "cumulative_world_count": 8,
        },
    ],
    "private_test_release_rule": (
        "after_all_required_checkpoints_and_world_seals_verified"
    ),
    "legacy_234_attempt_import_supported": False,
}


class StagedPilotError(RuntimeError):
    """Raised when staged execution cannot preserve the frozen protocol."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scientific_config(staged: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in staged.items() if key != "staging"}


def validate_staged_pilot_config(
    config: Mapping[str, Any] | str | Path = STAGED_PILOT_CONFIG_PATH,
) -> dict[str, Any]:
    if isinstance(config, (str, Path)):
        try:
            raw = json.loads(Path(config).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StagedPilotError("cannot read staged-pilot configuration") from exc
    else:
        try:
            raw = json.loads(canonical_json_bytes(config).decode("utf-8"))
        except (TypeError, ValueError, PilotCheckpointError) as exc:
            raise StagedPilotError("staged-pilot configuration is not finite JSON") from exc
    if not isinstance(raw, Mapping):
        raise StagedPilotError("staged-pilot configuration must be an object")
    staging = raw.get("staging")
    if staging != _EXPECTED_STAGING:
        raise StagedPilotError("staged-pilot boundaries or transaction contract drifted")
    scientific = validate_development_pilot_config(_scientific_config(raw))
    if scientific["model"]["provider"] != DEVELOPMENT_PILOT_VOLCENGINE_PROVIDER:
        raise StagedPilotError("staged pilot requires the frozen Volcengine profile")
    return dict(raw)


def _stage_id(world_index: int) -> str:
    if world_index < 2:
        return "S1"
    if world_index < 4:
        return "S2"
    return "S3"


def _build_plan(scientific: Mapping[str, Any]) -> list[dict[str, Any]]:
    config_hash = _config_sha256(scientific)
    arm_hashes = {
        arm_id: _hash({"arm_id": arm_id, "spec": arm_spec})
        for arm_id, arm_spec in scientific["arms"].items()
    }
    plan: list[dict[str, Any]] = []
    for world_index, world_spec in enumerate(scientific["worlds"]):
        world = generate_world(int(world_spec["seed"]), depth=int(world_spec["depth"]))
        world_hash = str(world.world_hash)
        order = _arm_execution_order(scientific["arms"], world_index)
        for arm_position, arm_id in enumerate(order):
            shard_index = world_index * SHARDS_PER_WORLD + arm_position
            arm_hash = arm_hashes[arm_id]
            plan.append(
                {
                    "shard_index": shard_index,
                    "world_index": world_index,
                    "world_seed": int(world_spec["seed"]),
                    "world_depth": int(world_spec["depth"]),
                    "world_hash": world_hash,
                    "arm_position": arm_position,
                    "arm_id": arm_id,
                    "arm_hash": arm_hash,
                    "run_id": _hash(
                        {
                            "config_hash": config_hash,
                            "world_hash": world_hash,
                            "arm_hash": arm_hash,
                        }
                    ),
                    "stage_id": _stage_id(world_index),
                    "logical_calls": CALLS_PER_SHARD,
                    "checkpoint_file": f"checkpoints/shard-{shard_index:02d}.json",
                }
            )
    if len(plan) != DEVELOPMENT_PILOT_EXPECTED_RUNS:
        raise StagedPilotError("campaign plan must contain exactly 56 shards")
    return plan


def _provenance_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left.get("source_manifest_sha256") == right.get("source_manifest_sha256")
        and left.get("files") == right.get("files")
        and left.get("environment") == right.get("environment")
    )


def _expected_provider_contract() -> dict[str, Any]:
    scientific = validate_development_pilot_config(
        _scientific_config(validate_staged_pilot_config())
    )
    dummy = ProviderCredentials(
        base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
        model=DEVELOPMENT_PILOT_MODEL,
        api_key="present-but-never-persisted",
    )
    return _public_provider_contract(scientific, dummy)


def initialize_campaign(
    campaign_dir: str | Path,
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = STAGED_PILOT_CONFIG_PATH,
    provenance_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    staged = validate_staged_pilot_config(config)
    scientific = _scientific_config(staged)
    preflight_development_pilot(credentials, config=scientific)
    provenance = (
        source_manifest(PROJECT_ROOT)
        if provenance_manifest is None
        else json.loads(canonical_json_bytes(provenance_manifest).decode("utf-8"))
    )
    plan = _build_plan(scientific)
    provider_contract = _public_provider_contract(scientific, credentials)
    payload = {
        "campaign_id": uuid.uuid4().hex,
        "created_at_utc": _utc_now(),
        "scientific_config": scientific,
        "scientific_config_sha256": _config_sha256(scientific),
        "staged_config_sha256": sha256_json(staged),
        "source_manifest": provenance,
        "source_manifest_sha256": provenance["source_manifest_sha256"],
        "provider_contract": provider_contract,
        "plan": plan,
        "plan_sha256": sha256_json(plan),
        "transaction_contract": {
            "calls_per_shard": CALLS_PER_SHARD,
            "shards_per_world_seal": SHARDS_PER_WORLD,
            "max_attempts_per_shard": MAX_ATTEMPTS_PER_SHARD,
            "checkpoint_is_commit_authority": True,
            "intra_episode_retry_supported": False,
            "intra_episode_resume_supported": False,
            "private_test_in_generation_process": False,
        },
        "decision_boundaries_world_count": list(DECISION_BOUNDARIES),
        "legacy_attempt_import": {
            "legacy_234_attempt_eligible": False,
            "imported_scientific_calls": 0,
            "reason": "legacy ledger lacks replayable atomic shard checkpoints",
        },
    }
    return publish_campaign_manifest(
        campaign_dir,
        payload,
        forbidden_values=(
            credentials.api_key,
            credentials.base_url,
            _normalize_endpoint_url(credentials.base_url),
        ),
    )


def _validate_manifest(
    manifest: Mapping[str, Any],
    *,
    staged: Mapping[str, Any],
    current_provenance: Mapping[str, Any],
    provider_contract: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    payload = manifest.get("payload")
    if not isinstance(payload, Mapping):
        raise StagedPilotError("campaign manifest payload is missing")
    scientific = _scientific_config(staged)
    expected_plan = _build_plan(scientific)
    if payload.get("scientific_config") != scientific:
        raise StagedPilotError("campaign scientific config drifted")
    if payload.get("scientific_config_sha256") != _config_sha256(scientific):
        raise StagedPilotError("campaign scientific config hash drifted")
    if payload.get("staged_config_sha256") != sha256_json(staged):
        raise StagedPilotError("campaign staged config hash drifted")
    frozen_provenance = payload.get("source_manifest")
    if not isinstance(frozen_provenance, Mapping) or not _provenance_equal(
        frozen_provenance, current_provenance
    ):
        raise StagedPilotError("campaign source manifest drifted")
    if payload.get("plan") != expected_plan or payload.get("plan_sha256") != sha256_json(
        expected_plan
    ):
        raise StagedPilotError("campaign 56-shard plan drifted")
    frozen_contract = payload.get("provider_contract")
    expected_contract = _expected_provider_contract()
    if frozen_contract != expected_contract:
        raise StagedPilotError("campaign provider contract is invalid or tampered")
    if provider_contract is not None and frozen_contract != provider_contract:
        raise StagedPilotError("runtime provider contract drifted from the campaign")
    legacy = payload.get("legacy_attempt_import")
    if not isinstance(legacy, Mapping) or (
        legacy.get("legacy_234_attempt_eligible") is not False
        or legacy.get("imported_scientific_calls") != 0
    ):
        raise StagedPilotError("legacy 234-call attempt import is forbidden")
    return payload


class ShardAttemptLedger:
    """Append-only operational ledger; never a scientific commit record."""

    def __init__(self, path: Path, *, shard: Mapping[str, Any], attempt: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self._handle = path.open("x", encoding="utf-8")
        os.chmod(path, 0o600)
        self.attempt_id = uuid.uuid4().hex
        self.append(
            {
                "event": "attempt_started",
                "at_utc": _utc_now(),
                "shard_index": shard["shard_index"],
                "world_index": shard["world_index"],
                "arm_id": shard["arm_id"],
                "attempt_number": attempt,
                "expected_calls": CALLS_PER_SHARD,
                "checkpoint_commit_authority": True,
            }
        )

    @property
    def closed(self) -> bool:
        return self._handle.closed

    def append(self, event: Mapping[str, Any]) -> None:
        row = {
            "schema_version": 1,
            "attempt_id": self.attempt_id,
            **dict(event),
        }
        encoded = canonical_json_bytes(row).decode("utf-8") + "\n"
        self._handle.write(encoded)
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()


class _ShardReporter:
    def __init__(self, ledger: ShardAttemptLedger, stream: TextIO) -> None:
        self.ledger = ledger
        self.stream = stream
        self.started = 0
        self.succeeded = 0

    def start(self, kwargs: Mapping[str, Any]) -> int:
        if self.started >= CALLS_PER_SHARD:
            raise StagedPilotError("shard exceeded its atomic 20-call budget")
        index = self.started
        expected_round, expected_candidate = divmod(index, 4)
        if (
            kwargs.get("round_index") != expected_round
            or kwargs.get("candidate_index") != expected_candidate
            or kwargs.get("max_output_tokens")
            != DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]
        ):
            raise StagedPilotError("shard call coordinates or output cap drifted")
        temperature = kwargs.get("temperature")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(float(temperature))
        ):
            raise StagedPilotError("shard supplied an invalid temperature")
        self.started += 1
        self.ledger.append(
            {
                "event": "logical_call_started",
                "at_utc": _utc_now(),
                "call_index": index,
                "round_index": expected_round,
                "candidate_index": expected_candidate,
                "temperature": float(temperature),
            }
        )
        return index

    def record(self, index: int, response: GenerationResponse) -> None:
        if index != self.succeeded:
            raise StagedPilotError("shard response order drifted")
        failures: list[str] = []
        if response.provider_model != DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL:
            failures.append("response_model_drift")
        if response.finish_reason != "stop":
            failures.append("finish_reason_drift")
        if response.provider_request_count != 1:
            failures.append("provider_retry_detected")
        if response.seed_supported is not False:
            failures.append("seed_support_drift")
        if response.candidate_format not in CANDIDATE_FORMATS:
            failures.append("candidate_format_metadata_invalid")
        if (
            response.prompt_cache_hit_tokens is not None
            or response.prompt_cache_miss_tokens is not None
        ):
            failures.append("unexpected_cache_telemetry")
        if response.provider_fingerprint is not None:
            failures.append("unexpected_system_fingerprint")
        if response.reasoning_tokens not in {None, 0}:
            failures.append("reasoning_tokens_nonzero")
        if response.output_tokens > DEVELOPMENT_PILOT_EPISODE["max_output_tokens"]:
            failures.append("output_token_cap_exceeded")
        self.ledger.append(
            {
                "event": "logical_call_succeeded",
                "at_utc": _utc_now(),
                "call_index": index,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": response.latency_ms,
                "provider_request_count": response.provider_request_count,
                "provider_model_matches": (
                    response.provider_model
                    == DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL
                ),
                "finish_reason_is_stop": response.finish_reason == "stop",
                "cache_fields_unavailable": (
                    response.prompt_cache_hit_tokens is None
                    and response.prompt_cache_miss_tokens is None
                ),
                "fingerprint_unavailable": response.provider_fingerprint is None,
                "candidate_format": response.candidate_format,
                "contract_failures": sorted(failures),
            }
        )
        if failures:
            raise StagedPilotError("provider response contract failed")
        self.succeeded += 1
        self.stream.write(
            f"[staged-pilot] shard-call {self.succeeded:02d}/{CALLS_PER_SHARD:02d} ok\n"
        )
        self.stream.flush()

    def fail(self, index: int, exc: BaseException) -> None:
        retry_eligible = (
            isinstance(exc, TransportError) and exc.delivery_ambiguous
        )
        self.ledger.append(
            {
                "event": "logical_call_failed_or_ambiguous",
                "at_utc": _utc_now(),
                "call_index": index,
                "failure_category": (
                    exc.category
                    if isinstance(exc, TransportError)
                    else "non_retryable_error"
                ),
                "delivery_ambiguous": retry_eligible,
                "retry_eligible": retry_eligible,
            }
        )


class _ReportedGenerator:
    def __init__(self, delegate: Any, reporter: _ShardReporter) -> None:
        self.delegate = delegate
        self.reporter = reporter

    def generate(self, *args: Any, **kwargs: Any) -> GenerationResponse:
        index = self.reporter.start(kwargs)
        try:
            response = self.delegate.generate(*args, **kwargs)
        except BaseException as exc:
            self.reporter.fail(index, exc)
            raise
        if not isinstance(response, GenerationResponse):
            self.reporter.fail(
                index,
                StagedPilotError("live shard generator returned an unmetered response"),
            )
            raise StagedPilotError("live shard generator returned an unmetered response")
        self.reporter.record(index, response)
        return response


def build_live_generator_factory(credentials: ProviderCredentials) -> Any:
    return OpenAICompatibleGeneratorFactory(
        base_url=credentials.base_url,
        api_key=credentials.api_key,
        model=DEVELOPMENT_PILOT_MODEL,
        seed_supported=False,
        evidence=False,
        mode=STAGED_PILOT_MODE,
        evidence_reason="staged eight-world development pilot; never confirmatory",
        timeout=60.0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _attempt_numbers(campaign_dir: Path, shard_index: int) -> list[int]:
    directory = campaign_dir / ATTEMPT_DIRECTORY_NAME
    if not directory.exists():
        return []
    result: list[int] = []
    for path in directory.glob(f"shard-{shard_index:02d}-attempt-*.jsonl"):
        match = _ATTEMPT_RE.fullmatch(path.name)
        if match is None or int(match.group(1)) != shard_index:
            raise StagedPilotError("attempt ledger filename is malformed")
        result.append(int(match.group(2)))
    result.sort()
    if result != list(range(1, len(result) + 1)):
        raise StagedPilotError("attempt ledger sequence has a gap")
    return result


def _prior_attempt_retry_eligible(
    campaign_dir: Path,
    shard_index: int,
    attempt_number: int,
) -> bool:
    """Allow only delivery-ambiguous or unclean-process whole-shard restart."""

    rows = _read_attempt_rows(
        attempt_ledger_path(campaign_dir, shard_index, attempt_number)
    )
    abandoned = [row for row in rows if row.get("event") == "attempt_abandoned"]
    if abandoned:
        return abandoned[-1].get("retry_eligible") is True
    failures = [
        row
        for row in rows
        if row.get("event") == "logical_call_failed_or_ambiguous"
    ]
    if failures:
        return failures[-1].get("retry_eligible") is True
    # A process may die after a request start or response but before writing a
    # terminal event.  The only safe recovery is a full, explicit shard restart.
    return True


def _normalize_checkpoint_run(run: Mapping[str, Any]) -> dict[str, Any]:
    result = json.loads(canonical_json_bytes(run).decode("utf-8"))
    result.pop("final_test", None)
    budget = result.get("budget")
    if isinstance(budget, dict):
        budget.pop("final_test_points_planned", None)
        budget.pop("final_test_points_evaluated", None)
    for candidate in result.get("candidates", []):
        if candidate.get("syntax_valid") is not True:
            candidate["candidate_expression"] = INVALID_CANDIDATE_SENTINEL
            candidate["canonical_hash"] = _stable_hash(INVALID_CANDIDATE_SENTINEL)
            candidate["behavior_hash"] = ""
            candidate["node_count"] = 10**9
    return result


def _checkpoint_payload(
    *,
    manifest: Mapping[str, Any],
    plan_entry: Mapping[str, Any],
    attempt_number: int,
    run: Mapping[str, Any],
) -> dict[str, Any]:
    payload = manifest["payload"]
    return {
        "campaign_id": payload["campaign_id"],
        "campaign_manifest_payload_sha256": manifest["payload_sha256"],
        "scientific_config_sha256": payload["scientific_config_sha256"],
        "source_manifest_sha256": payload["source_manifest_sha256"],
        "provider_contract_sha256": sha256_json(payload["provider_contract"]),
        "plan_sha256": payload["plan_sha256"],
        "shard_index": plan_entry["shard_index"],
        "world_index": plan_entry["world_index"],
        "arm_position": plan_entry["arm_position"],
        "arm_id": plan_entry["arm_id"],
        "attempt_number": attempt_number,
        "committed_scientific_calls": CALLS_PER_SHARD,
        "private_test_evaluated": False,
        "run": _normalize_checkpoint_run(run),
    }


def _validate_checkpoint_against_plan(
    checkpoint: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    plan_entry: Mapping[str, Any],
) -> None:
    payload = checkpoint.get("payload")
    manifest_payload = manifest["payload"]
    if not isinstance(payload, Mapping):
        raise StagedPilotError("checkpoint payload is missing")
    exact = {
        "campaign_id": manifest_payload["campaign_id"],
        "campaign_manifest_payload_sha256": manifest["payload_sha256"],
        "scientific_config_sha256": manifest_payload["scientific_config_sha256"],
        "source_manifest_sha256": manifest_payload["source_manifest_sha256"],
        "provider_contract_sha256": sha256_json(
            manifest_payload["provider_contract"]
        ),
        "plan_sha256": manifest_payload["plan_sha256"],
        "shard_index": plan_entry["shard_index"],
        "world_index": plan_entry["world_index"],
        "arm_position": plan_entry["arm_position"],
        "arm_id": plan_entry["arm_id"],
        "committed_scientific_calls": CALLS_PER_SHARD,
        "private_test_evaluated": False,
    }
    if any(payload.get(key) != value for key, value in exact.items()):
        raise StagedPilotError("checkpoint identity/config/plan binding drifted")
    if (
        type(payload.get("attempt_number")) is not int
        or not 1 <= payload["attempt_number"] <= MAX_ATTEMPTS_PER_SHARD
    ):
        raise StagedPilotError("checkpoint attempt number is invalid")
    run = payload.get("run")
    if not isinstance(run, Mapping):
        raise StagedPilotError("checkpoint run is missing")
    if (
        run.get("run_id") != plan_entry["run_id"]
        or run.get("arm_id") != plan_entry["arm_id"]
        or run.get("world", {}).get("index") != plan_entry["world_index"]
        or run.get("world", {}).get("seed") != plan_entry["world_seed"]
        or run.get("world", {}).get("depth") != plan_entry["world_depth"]
        or len(run.get("candidates", [])) != CALLS_PER_SHARD
        or "final_test" in run
    ):
        raise StagedPilotError("checkpoint scientific run identity is malformed")


def _checkpoint_indices(campaign_dir: Path) -> list[int]:
    directory = campaign_dir / "checkpoints"
    if not directory.exists():
        return []
    indices: list[int] = []
    for path in directory.glob("shard-*.json"):
        match = _CHECKPOINT_RE.fullmatch(path.name)
        if match is None:
            raise StagedPilotError("checkpoint filename is malformed")
        index = int(match.group(1))
        if not 0 <= index < DEVELOPMENT_PILOT_EXPECTED_RUNS:
            raise StagedPilotError("checkpoint shard index is outside the plan")
        indices.append(index)
    indices.sort()
    if indices != list(range(len(indices))):
        raise StagedPilotError("checkpoint sequence has a gap")
    return indices


def _world_seal_payload(
    campaign_dir: Path,
    *,
    manifest: Mapping[str, Any],
    world_index: int,
) -> dict[str, Any]:
    first = world_index * SHARDS_PER_WORLD
    references: list[dict[str, Any]] = []
    for shard_index in range(first, first + SHARDS_PER_WORLD):
        checkpoint = load_shard_checkpoint(campaign_dir, shard_index)
        _validate_checkpoint_against_plan(
            checkpoint,
            manifest=manifest,
            plan_entry=manifest["payload"]["plan"][shard_index],
        )
        path = checkpoint_path(campaign_dir, shard_index)
        references.append(
            {
                "shard_index": shard_index,
                "checkpoint_file": path.relative_to(campaign_dir).as_posix(),
                "checkpoint_file_sha256": sha256_bytes(path.read_bytes()),
                "checkpoint_payload_sha256": checkpoint["payload_sha256"],
            }
        )
    return {
        "campaign_id": manifest["payload"]["campaign_id"],
        "campaign_manifest_payload_sha256": manifest["payload_sha256"],
        "world_index": world_index,
        "world_seed": manifest["payload"]["plan"][first]["world_seed"],
        "world_hash": manifest["payload"]["plan"][first]["world_hash"],
        "shard_count": SHARDS_PER_WORLD,
        "committed_scientific_calls": SHARDS_PER_WORLD * CALLS_PER_SHARD,
        "checkpoint_references": references,
    }


def ensure_world_seals(campaign_dir: str | Path, manifest: Mapping[str, Any]) -> None:
    root = Path(campaign_dir)
    committed = len(_checkpoint_indices(root))
    completed_worlds = committed // SHARDS_PER_WORLD
    for world_index in range(completed_worlds):
        expected = _world_seal_payload(root, manifest=manifest, world_index=world_index)
        path = world_seal_path(root, world_index)
        if path.exists() or path.is_symlink():
            seal = load_world_seal(root, world_index)
            if seal.get("payload") != expected:
                raise StagedPilotError("world seal is tampered or stale")
        else:
            publish_world_seal(root, expected)


def audit_campaign(
    campaign_dir: str | Path,
    *,
    manifest: Mapping[str, Any] | None = None,
    require_world_seals: bool = True,
) -> dict[str, Any]:
    root = Path(campaign_dir)
    loaded_manifest = load_campaign_manifest(root) if manifest is None else manifest
    indices = _checkpoint_indices(root)
    for shard_index in indices:
        checkpoint = load_shard_checkpoint(root, shard_index)
        _validate_checkpoint_against_plan(
            checkpoint,
            manifest=loaded_manifest,
            plan_entry=loaded_manifest["payload"]["plan"][shard_index],
        )
    completed_worlds = len(indices) // SHARDS_PER_WORLD
    sealed_worlds: list[int] = []
    for world_index in range(completed_worlds):
        path = world_seal_path(root, world_index)
        if not path.exists():
            if require_world_seals:
                raise StagedPilotError("complete world lacks its immutable seal")
            continue
        seal = load_world_seal(root, world_index)
        expected = _world_seal_payload(
            root,
            manifest=loaded_manifest,
            world_index=world_index,
        )
        if seal.get("payload") != expected:
            raise StagedPilotError("world seal checkpoint hashes drifted")
        sealed_worlds.append(world_index)
    return {
        "committed_shard_count": len(indices),
        "committed_scientific_calls": len(indices) * CALLS_PER_SHARD,
        "completed_world_count": completed_worlds,
        "sealed_world_indices": sealed_worlds,
        "next_shard_index": len(indices),
        "at_decision_boundary": len(indices) in CHECKPOINT_BOUNDARIES,
    }


def _run_shard_episode(
    *,
    scientific: Mapping[str, Any],
    plan_entry: Mapping[str, Any],
    generator_factory: Any,
    reporter: _ShardReporter,
) -> dict[str, Any]:
    world = generate_world(
        int(plan_entry["world_seed"]), depth=int(plan_entry["world_depth"])
    )
    episode = scientific["episode"]
    context = GeneratorContext(
        experiment=str(scientific["experiment"]),
        episode=MappingProxyType(dict(episode)),
        model=MappingProxyType(dict(scientific["model"])),
        max_output_tokens=int(episode["max_output_tokens"]),
    )
    delegate = _call_generator_factory(generator_factory, context)
    verifier = Verifier(
        counterexample_limit=int(episode["max_counterexamples_per_round"])
    )
    result = run_episode(
        world,
        _ReportedGenerator(delegate, reporter),
        verifier=verifier,
        policy=_policy_from_config(
            str(plan_entry["arm_id"]),
            scientific["arms"][str(plan_entry["arm_id"])],
        ),
        rounds=int(episode["rounds"]),
        candidates_per_round=int(episode["candidates_per_round"]),
        archive_capacity=int(episode["archive_size"]),
        max_counterexamples=(
            int(episode["rounds"])
            * int(episode["max_counterexamples_per_round"])
        ),
        max_output_tokens=int(episode["max_output_tokens"]),
        max_counterexamples_per_round=int(
            episode["max_counterexamples_per_round"]
        ),
        seed=SAMPLING_BASE_SEED,
        evaluate_test=False,
    )
    if reporter.started != CALLS_PER_SHARD or reporter.succeeded != CALLS_PER_SHARD:
        raise StagedPilotError("atomic shard did not complete exactly 20 calls")
    return _run_summary(
        context=context,
        result=result,
        run_id=str(plan_entry["run_id"]),
        arm_id=str(plan_entry["arm_id"]),
        arm_hash=str(plan_entry["arm_hash"]),
        world_index=int(plan_entry["world_index"]),
        world_seed=int(plan_entry["world_seed"]),
        world_depth=int(plan_entry["world_depth"]),
        world_hash=str(plan_entry["world_hash"]),
        sampling_base_seed=SAMPLING_BASE_SEED,
        probe_size=len(tuple(world.probe)),
        test_size=len(tuple(world.test)),
        max_counterexamples_per_round=int(
            episode["max_counterexamples_per_round"]
        ),
    )


def run_next_shard(
    campaign_dir: str | Path,
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = STAGED_PILOT_CONFIG_PATH,
    generator_factory: Any | None = None,
    provenance_manifest: Mapping[str, Any] | None = None,
    resume: bool = False,
    allow_boundary_cross: bool = False,
    progress_stream: TextIO | None = None,
    crash_at: str | None = None,
) -> dict[str, Any]:
    """Run and atomically commit the next 20-call shard.

    ``crash_at`` is an offline test hook accepting ``before_commit`` or
    ``after_commit``.  Production callers leave it ``None``.
    """

    staged = validate_staged_pilot_config(config)
    scientific = _scientific_config(staged)
    preflight_development_pilot(credentials, config=scientific)
    current_provenance = (
        source_manifest(PROJECT_ROOT)
        if provenance_manifest is None
        else provenance_manifest
    )
    root = Path(campaign_dir)
    manifest_path = root / CAMPAIGN_MANIFEST_NAME
    if not manifest_path.exists():
        if resume:
            raise StagedPilotError("cannot resume a campaign without a manifest")
        initialize_campaign(
            root,
            credentials,
            config=staged,
            provenance_manifest=current_provenance,
        )
    manifest = load_campaign_manifest(root)
    runtime_contract = _public_provider_contract(scientific, credentials)
    _validate_manifest(
        manifest,
        staged=staged,
        current_provenance=current_provenance,
        provider_contract=runtime_contract,
    )
    ensure_world_seals(root, manifest)
    state = audit_campaign(root, manifest=manifest, require_world_seals=True)
    shard_index = int(state["next_shard_index"])
    if shard_index >= DEVELOPMENT_PILOT_EXPECTED_RUNS:
        return {**state, "status": "campaign_complete"}
    if state["at_decision_boundary"] and not allow_boundary_cross:
        return {**state, "status": "decision_boundary"}
    prior_attempts = _attempt_numbers(root, shard_index)
    if prior_attempts and not resume:
        raise StagedPilotError(
            "uncommitted shard has an abandoned attempt; explicit resume is required"
        )
    if prior_attempts and not _prior_attempt_retry_eligible(
        root, shard_index, prior_attempts[-1]
    ):
        raise StagedPilotError(
            "previous shard failure is campaign-fatal and cannot be resumed"
        )
    if len(prior_attempts) >= MAX_ATTEMPTS_PER_SHARD:
        raise StagedPilotError("shard exhausted its maximum of three attempts")
    attempt_number = len(prior_attempts) + 1
    plan_entry = manifest["payload"]["plan"][shard_index]
    ledger = ShardAttemptLedger(
        attempt_ledger_path(root, shard_index, attempt_number),
        shard=plan_entry,
        attempt=attempt_number,
    )
    stream = sys.stderr if progress_stream is None else progress_stream
    reporter = _ShardReporter(ledger, stream)
    factory = (
        build_live_generator_factory(credentials)
        if generator_factory is None
        else generator_factory
    )
    try:
        run = _run_shard_episode(
            scientific=scientific,
            plan_entry=plan_entry,
            generator_factory=factory,
            reporter=reporter,
        )
        if crash_at == "before_commit":
            raise StagedPilotError("injected crash before checkpoint commit")
        checkpoint = publish_shard_checkpoint(
            root,
            _checkpoint_payload(
                manifest=manifest,
                plan_entry=plan_entry,
                attempt_number=attempt_number,
                run=run,
            ),
            forbidden_values=(
                credentials.api_key,
                credentials.base_url,
                _normalize_endpoint_url(credentials.base_url),
            ),
        )
        if crash_at == "after_commit":
            raise StagedPilotError("injected crash after checkpoint commit")
        ledger.append(
            {
                "event": "checkpoint_committed",
                "at_utc": _utc_now(),
                "checkpoint_payload_sha256": checkpoint["payload_sha256"],
                "committed_scientific_calls": CALLS_PER_SHARD,
            }
        )
    except BaseException as exc:
        if not ledger.closed:
            try:
                retry_eligible = (
                    isinstance(exc, TransportError) and exc.delivery_ambiguous
                ) or (
                    crash_at == "before_commit"
                    and isinstance(exc, StagedPilotError)
                )
                ledger.append(
                    {
                        "event": "attempt_abandoned",
                        "at_utc": _utc_now(),
                        "checkpoint_present": checkpoint_path(root, shard_index).exists(),
                        "retry_eligible": retry_eligible,
                        "recovery_scope": (
                            "restart_whole_shard"
                            if retry_eligible
                            else "campaign_fatal"
                        ),
                    }
                )
            finally:
                ledger.close()
        raise
    ledger.close()
    ensure_world_seals(root, manifest)
    updated = audit_campaign(root, manifest=manifest, require_world_seals=True)
    return {
        **updated,
        "status": (
            "campaign_complete"
            if updated["next_shard_index"] == DEVELOPMENT_PILOT_EXPECTED_RUNS
            else "decision_boundary"
            if updated["at_decision_boundary"]
            else "shard_committed"
        ),
        "committed_shard_index": shard_index,
        "attempt_number": attempt_number,
    }


def run_stage(
    campaign_dir: str | Path,
    credentials: ProviderCredentials,
    *,
    config: Mapping[str, Any] | str | Path = STAGED_PILOT_CONFIG_PATH,
    generator_factory: Any | None = None,
    provenance_manifest: Mapping[str, Any] | None = None,
    resume: bool = False,
    progress_stream: TextIO | None = None,
) -> dict[str, Any]:
    """Run from the current commit through exactly the next frozen boundary."""

    root = Path(campaign_dir)
    if (root / CAMPAIGN_MANIFEST_NAME).exists():
        manifest = load_campaign_manifest(root)
        state = audit_campaign(root, manifest=manifest, require_world_seals=False)
        start = int(state["next_shard_index"])
        if start and not resume:
            raise StagedPilotError("continuing a campaign requires explicit resume")
    else:
        start = 0
        if resume:
            raise StagedPilotError("cannot resume a campaign without a manifest")
    target = next((value for value in CHECKPOINT_BOUNDARIES if value > start), None)
    if target is None:
        return {
            "status": "campaign_complete",
            "next_shard_index": DEVELOPMENT_PILOT_EXPECTED_RUNS,
            "committed_shard_count": DEVELOPMENT_PILOT_EXPECTED_RUNS,
        }
    latest: dict[str, Any] = {}
    while True:
        latest = run_next_shard(
            root,
            credentials,
            config=config,
            generator_factory=generator_factory,
            provenance_manifest=provenance_manifest,
            resume=(resume or bool(latest)),
            allow_boundary_cross=True,
            progress_stream=progress_stream,
        )
        if int(latest["next_shard_index"]) >= target:
            return latest


class _ReplayGenerator:
    def __init__(self, candidates: Sequence[Mapping[str, Any]]) -> None:
        self._candidates = list(candidates)
        self._index = 0

    def generate(self, prompt: str, **kwargs: Any) -> GenerationResponse:
        del prompt, kwargs
        if self._index >= len(self._candidates):
            raise StagedPilotError("checkpoint replay exhausted its candidate script")
        candidate = self._candidates[self._index]
        self._index += 1
        return GenerationResponse(
            expression=candidate["candidate_expression"],
            input_tokens=int(candidate["input_tokens"]),
            output_tokens=int(candidate["output_tokens"]),
            latency_ms=float(candidate["latency_ms"]),
            provider_request_count=int(candidate["provider_request_count"]),
            seed_supported=candidate["seed_supported"],
            provider_model=candidate["provider_model"],
            finish_reason=candidate["finish_reason"],
            prompt_cache_hit_tokens=candidate["prompt_cache_hit_tokens"],
            prompt_cache_miss_tokens=candidate["prompt_cache_miss_tokens"],
            reasoning_tokens=candidate["reasoning_tokens"],
            candidate_format=candidate["candidate_format"],
            provider_fingerprint=candidate["provider_fingerprint"],
        )


def _replay_checkpoint(
    checkpoint: Mapping[str, Any],
    *,
    scientific: Mapping[str, Any],
    plan_entry: Mapping[str, Any],
) -> tuple[Any, Verifier, dict[str, Any]]:
    checkpoint_run = checkpoint["payload"]["run"]
    world = generate_world(
        int(plan_entry["world_seed"]), depth=int(plan_entry["world_depth"])
    )
    episode = scientific["episode"]
    context = GeneratorContext(
        experiment=str(scientific["experiment"]),
        episode=MappingProxyType(dict(episode)),
        model=MappingProxyType(dict(scientific["model"])),
        max_output_tokens=int(episode["max_output_tokens"]),
    )
    verifier = Verifier(
        counterexample_limit=int(episode["max_counterexamples_per_round"])
    )
    result = run_episode(
        world,
        _ReplayGenerator(checkpoint_run["candidates"]),
        verifier=verifier,
        policy=_policy_from_config(
            str(plan_entry["arm_id"]),
            scientific["arms"][str(plan_entry["arm_id"])],
        ),
        rounds=int(episode["rounds"]),
        candidates_per_round=int(episode["candidates_per_round"]),
        archive_capacity=int(episode["archive_size"]),
        max_counterexamples=(
            int(episode["rounds"])
            * int(episode["max_counterexamples_per_round"])
        ),
        max_output_tokens=int(episode["max_output_tokens"]),
        max_counterexamples_per_round=int(
            episode["max_counterexamples_per_round"]
        ),
        seed=SAMPLING_BASE_SEED,
        evaluate_test=False,
    )
    summary_kwargs = {
        "context": context,
        "run_id": str(plan_entry["run_id"]),
        "arm_id": str(plan_entry["arm_id"]),
        "arm_hash": str(plan_entry["arm_hash"]),
        "world_index": int(plan_entry["world_index"]),
        "world_seed": int(plan_entry["world_seed"]),
        "world_depth": int(plan_entry["world_depth"]),
        "world_hash": str(plan_entry["world_hash"]),
        "sampling_base_seed": SAMPLING_BASE_SEED,
        "probe_size": len(tuple(world.probe)),
        "test_size": len(tuple(world.test)),
        "max_counterexamples_per_round": int(
            episode["max_counterexamples_per_round"]
        ),
    }
    replayed = _normalize_checkpoint_run(
        _run_summary(result=result, **summary_kwargs)
    )
    if replayed != checkpoint_run:
        raise StagedPilotError(
            "offline replay disagrees with checkpoint prompts, validity, hashes, "
            "trajectory, or selected candidate"
        )
    return result, verifier, summary_kwargs


def _read_attempt_rows(path: Path) -> list[Mapping[str, Any]]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StagedPilotError("attempt ledger is malformed") from exc
    if any(not isinstance(row, Mapping) for row in rows):
        raise StagedPilotError("attempt ledger row is malformed")
    return rows


def _execution_audit(
    campaign_dir: Path,
    checkpoints: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    required = len(checkpoints)
    committed_attempts = {
        (int(item["payload"]["shard_index"]), int(item["payload"]["attempt_number"]))
        for item in checkpoints
    }
    abandoned_calls = 0
    abandoned_attempts = 0
    discarded_calls = 0
    discarded_known_tokens = 0
    ambiguous_calls = 0
    attempt_count = 0
    directory = campaign_dir / ATTEMPT_DIRECTORY_NAME
    if directory.exists():
        for path in sorted(directory.glob("shard-*-attempt-*.jsonl")):
            match = _ATTEMPT_RE.fullmatch(path.name)
            if match is None:
                raise StagedPilotError("attempt ledger filename is malformed")
            shard_index = int(match.group(1))
            attempt_number = int(match.group(2))
            if shard_index >= required:
                continue
            attempt_count += 1
            rows = _read_attempt_rows(path)
            if (shard_index, attempt_number) in committed_attempts:
                continue
            abandoned_attempts += 1
            starts = sum(row.get("event") == "logical_call_started" for row in rows)
            success_rows = [
                row for row in rows if row.get("event") == "logical_call_succeeded"
            ]
            successes = len(success_rows)
            explicit_ambiguous = sum(
                row.get("event") == "logical_call_failed_or_ambiguous"
                and row.get("delivery_ambiguous") is True
                for row in rows
            )
            unresolved = max(0, starts - successes - explicit_ambiguous)
            abandoned_calls += starts
            discarded_calls += successes
            ambiguous_calls += explicit_ambiguous + unresolved
            discarded_known_tokens += sum(
                int(row.get("input_tokens", 0)) + int(row.get("output_tokens", 0))
                for row in success_rows
            )
    accepted_known_tokens = sum(
        int(item["payload"]["run"]["budget"]["actual_billed_tokens"])
        for item in checkpoints
    )
    recovery_used = abandoned_attempts > 0
    return {
        "committed_scientific_calls": required * CALLS_PER_SHARD,
        "committed_shards": required,
        "committed_worlds": required // SHARDS_PER_WORLD,
        "attempt_ledgers_observed": attempt_count,
        "physical_request_starts": required * CALLS_PER_SHARD + abandoned_calls,
        "abandoned_shard_attempts": abandoned_attempts,
        "abandoned_operational_calls": abandoned_calls,
        "discarded_operational_calls": discarded_calls,
        "ambiguous_operational_calls": ambiguous_calls,
        "accepted_known_tokens": accepted_known_tokens,
        "discarded_known_tokens": discarded_known_tokens,
        "gross_known_token_lower_bound": (
            accepted_known_tokens + discarded_known_tokens
        ),
        "gross_usage_complete": ambiguous_calls == 0,
        "recovery_used": recovery_used,
        "actual_token_matched_claim_allowed": not recovery_used,
        "primary_estimand": (
            "first-complete-episode-under-frozen-recovery-policy"
            if recovery_used
            else "frozen-world-arm-grid"
        ),
        "operational_cost_is_not_scientific_sample_size": True,
        "legacy_234_attempt_imported": False,
        "legacy_234_attempt_scientific_calls": 0,
    }


def finalize_snapshot(
    campaign_dir: str | Path,
    cumulative_world_count: int,
    *,
    config: Mapping[str, Any] | str | Path = STAGED_PILOT_CONFIG_PATH,
    current_source_manifest: Mapping[str, Any] | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify, replay, then evaluate a sealed 2/4/8-world snapshot offline."""

    if cumulative_world_count not in DECISION_BOUNDARIES:
        raise StagedPilotError("snapshot world count must be exactly 2, 4, or 8")
    root = Path(campaign_dir)
    staged = validate_staged_pilot_config(config)
    scientific = _scientific_config(staged)
    manifest = load_campaign_manifest(root)
    current_provenance = (
        source_manifest(PROJECT_ROOT)
        if current_source_manifest is None
        else current_source_manifest
    )
    manifest_payload = _validate_manifest(
        manifest,
        staged=staged,
        current_provenance=current_provenance,
    )
    state = audit_campaign(root, manifest=manifest, require_world_seals=True)
    required_shards = cumulative_world_count * SHARDS_PER_WORLD
    if state["committed_shard_count"] < required_shards:
        raise StagedPilotError("snapshot boundary lacks all required shard checkpoints")
    if state["sealed_world_indices"][:cumulative_world_count] != list(
        range(cumulative_world_count)
    ):
        raise StagedPilotError("snapshot boundary lacks all required world seals")

    checkpoints: list[dict[str, Any]] = []
    pending: list[tuple[Any, Verifier, dict[str, Any]]] = []
    # No private-test function is called before every required commit has been
    # loaded, hash-checked, plan-checked, and replayed successfully.
    for shard_index in range(required_shards):
        checkpoint = load_shard_checkpoint(root, shard_index)
        _validate_checkpoint_against_plan(
            checkpoint,
            manifest=manifest,
            plan_entry=manifest_payload["plan"][shard_index],
        )
        checkpoints.append(checkpoint)
    for shard_index, checkpoint in enumerate(checkpoints):
        pending.append(
            _replay_checkpoint(
                checkpoint,
                scientific=scientific,
                plan_entry=manifest_payload["plan"][shard_index],
            )
        )

    run_summaries: list[dict[str, Any]] = []
    for result, verifier, summary_kwargs in pending:
        result.final_test = evaluate_episode_test(result, verifier=verifier)
        run_summaries.append(_run_summary(result=result, **summary_kwargs))

    arm_hashes = {
        arm_id: _hash({"arm_id": arm_id, "spec": arm_spec})
        for arm_id, arm_spec in scientific["arms"].items()
    }
    world_summaries: list[dict[str, Any]] = []
    for world_index, world_spec in enumerate(
        scientific["worlds"][:cumulative_world_count]
    ):
        world = generate_world(int(world_spec["seed"]), depth=int(world_spec["depth"]))
        world_summaries.append(
            {
                "index": world_index,
                "seed": int(world_spec["seed"]),
                "depth": int(world_spec["depth"]),
                "world_hash": str(world.world_hash),
                "arm_execution_order": list(
                    _arm_execution_order(scientific["arms"], world_index)
                ),
            }
        )
    budget = _aggregate_budget(run_summaries)
    observed_models = sorted(
        {
            str(candidate["provider_model"])
            for run in run_summaries
            for candidate in run["candidates"]
            if candidate.get("provider_model")
        }
    )
    observed_fingerprints = sorted(
        {
            str(candidate["provider_fingerprint"])
            for run in run_summaries
            for candidate in run["candidates"]
            if candidate.get("provider_fingerprint")
        }
    )
    finish_counts: dict[str, int] = {}
    for run in run_summaries:
        for candidate in run["candidates"]:
            reason = candidate.get("finish_reason")
            if reason:
                finish_counts[str(reason)] = finish_counts.get(str(reason), 0) + 1
            candidate.pop("candidate_expression", None)
    stage_id = {2: "S1", 4: "S2", 8: "S3"}[cumulative_world_count]
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "kind": "staged-development-pilot-snapshot",
        "experiment": scientific["experiment"],
        "config_status": scientific["status"],
        "config_hash": _config_sha256(scientific),
        "evidence": False,
        "evidence_scope": "non-evidence",
        "evidence_reason": (
            "outcome-observed staged development snapshot; never confirmatory evidence"
        ),
        "mode": OFFLINE_FINALIZED_MODE,
        "model": {
            "configured": dict(scientific["model"]),
            "observed_response_models": observed_models,
            "observed_system_fingerprints": observed_fingerprints,
            "finish_reason_counts": finish_counts,
        },
        "arms": [
            {"arm_id": arm_id, "arm_hash": arm_hashes[arm_id]}
            for arm_id in scientific["arms"]
        ],
        "arm_hashes": arm_hashes,
        "worlds": world_summaries,
        "world_hashes": [item["world_hash"] for item in world_summaries],
        "runs": run_summaries,
        "budget": budget,
        "stage": {
            "stage_id": stage_id,
            "cumulative_world_count": cumulative_world_count,
            "included_world_indices": list(range(cumulative_world_count)),
            "required_checkpoint_count": required_shards,
            "required_world_seal_count": cumulative_world_count,
            "final_classification_eligible": cumulative_world_count == 8,
            "private_test_release_rule": (
                "after_all_required_checkpoints_and_world_seals_verified"
            ),
            "optional_stopping_present": True,
            "inference_scope": (
                "preliminary-development-only"
                if cumulative_world_count == 8
                else "exploratory-development-interim"
            ),
        },
        "execution_audit": _execution_audit(root, checkpoints),
        "provider_contract": manifest_payload["provider_contract"],
        "provenance": manifest_payload["source_manifest"],
        "campaign": {
            "campaign_id": manifest_payload["campaign_id"],
            "manifest_sha256": sha256_bytes(
                (root / CAMPAIGN_MANIFEST_NAME).read_bytes()
            ),
            "config_sha256": manifest_payload["scientific_config_sha256"],
            "staged_config_sha256": manifest_payload["staged_config_sha256"],
            "source_manifest_sha256": manifest_payload[
                "source_manifest_sha256"
            ],
            "plan_sha256": manifest_payload["plan_sha256"],
        },
    }
    encoded = canonical_json_bytes(snapshot).decode("utf-8")
    if "candidate_expression" in encoded:
        raise StagedPilotError("public snapshot retained a candidate expression")
    if DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT in encoded:
        raise StagedPilotError("public snapshot retained a raw provider endpoint")
    if output_path is not None:
        atomic_publish_public_snapshot(output_path, snapshot)
    return snapshot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--campaign-dir", type=Path, required=True)
    generate.add_argument("--config", type=Path, default=STAGED_PILOT_CONFIG_PATH)
    generate.add_argument("--env-file", type=Path, required=True)
    generate.add_argument("--env-prefix", default="VOLCENGINE")
    generate.add_argument("--execute", action="store_true")
    generate.add_argument("--resume", action="store_true")

    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--campaign-dir", type=Path, required=True)
    finalize.add_argument("--config", type=Path, default=STAGED_PILOT_CONFIG_PATH)
    finalize.add_argument("--world-count", type=int, choices=DECISION_BOUNDARIES, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "finalize":
        snapshot = finalize_snapshot(
            args.campaign_dir,
            args.world_count,
            config=args.config,
            output_path=args.output,
        )
        print(
            json.dumps(
                {
                    "status": "snapshot_finalized",
                    "world_count": snapshot["stage"]["cumulative_world_count"],
                    "output": str(args.output),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not args.execute:
        _parser().error("refusing external API use without --execute")
    credentials = load_provider_credentials(
        prefix=args.env_prefix,
        env_file=args.env_file,
    )
    try:
        result = run_stage(
            args.campaign_dir,
            credentials,
            config=args.config,
            resume=args.resume,
            progress_stream=sys.stderr,
        )
    except BaseException:
        print("[staged-pilot] stage aborted safely", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


__all__ = [
    "CALLS_PER_SHARD",
    "CHECKPOINT_BOUNDARIES",
    "DECISION_BOUNDARIES",
    "MAX_ATTEMPTS_PER_SHARD",
    "OFFLINE_FINALIZED_MODE",
    "SHARDS_PER_WORLD",
    "STAGED_PILOT_CONFIG_PATH",
    "STAGED_PILOT_MODE",
    "ShardAttemptLedger",
    "StagedPilotError",
    "audit_campaign",
    "build_live_generator_factory",
    "ensure_world_seals",
    "finalize_snapshot",
    "initialize_campaign",
    "main",
    "run_next_shard",
    "run_stage",
    "validate_staged_pilot_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
