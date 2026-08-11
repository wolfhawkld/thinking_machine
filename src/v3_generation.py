"""Generation-only helpers for the frozen V3 development campaign.

The public entrypoint in this module is deliberately kept separate from the
private-test finalizer.  Generation receives a view with train/probe data and
the full public domain, but no ``test`` or hidden ``law`` attribute.  Episode
seals expose only aggregate scalar diagnostics plus a digest of the exact
runner state; candidate expressions and candidate identity hashes remain in
the mode-0600 call checkpoints that the digest binds indirectly.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .experiment import (
    _failure_counts,
    _policy_from_config,
    _round_best_scores,
    _sanitized_controller_trace,
)
from .pilot_checkpoint import sha256_bytes, sha256_json
from .providers.openai_compatible import OpenAICompatibleGenerator
from .provenance import PROJECT_ROOT, source_manifest
from .runner import EpisodeResult, GenerationResponse, run_episode
from .staged_pilot_v3 import (
    AcceptedResponseContract,
    DurableLogicalSlotGenerator,
    FrozenTransactionIdentity,
    route_binding_sha256,
)
from .v3_development import (
    V3DevelopmentError,
    transaction_identity_payload,
    validate_campaign_manifest,
)
from .world_generator import Example, generate_world
from .verifier import Verifier


class V3GenerationError(RuntimeError):
    """Raised before a V3 generation shard can become scientific state."""


_STABLE_EXECUTION_AUDIT_FIELDS = (
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
)


@dataclass(frozen=True)
class GenerationWorldView:
    """The exact world surface available before the generation barrier."""

    seed: int
    train: tuple[Example, ...]
    probe: tuple[Example, ...]
    depth_tier: int
    world_hash: str
    domain: tuple[tuple[int, int, int], ...]

    @property
    def train_examples(self) -> tuple[Example, ...]:
        return self.train

    @property
    def probe_examples(self) -> tuple[Example, ...]:
        return self.probe


class _CommittedCheckpointReplayGenerator:
    """Consume exactly 20 normalized checkpoints without transport access."""

    def __init__(self, checkpoints: Sequence[Mapping[str, Any]]) -> None:
        if len(checkpoints) != 20:
            raise V3GenerationError("generation replay requires exactly 20 checkpoints")
        self.checkpoints = tuple(checkpoints)
        self.calls = 0

    def generate(
        self,
        prompt: str,
        *,
        temperature: float,
        max_output_tokens: int = 256,
        round_index: int = 0,
        candidate_index: int = 0,
        seed: int | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> GenerationResponse:
        del temperature, seed, state
        slot = self.calls
        if slot >= 20:
            raise V3GenerationError("generation replay exceeded 20 checkpoints")
        if (round_index, candidate_index) != divmod(slot, 4):
            raise V3GenerationError("generation replay logical-slot order drifted")
        if max_output_tokens != 256:
            raise V3GenerationError("generation replay output-token cap drifted")
        checkpoint = self.checkpoints[slot]
        if checkpoint.get("slot_index") != slot:
            raise V3GenerationError("generation replay checkpoint order drifted")
        if checkpoint.get("prompt_sha256") != sha256_bytes(prompt.encode("utf-8")):
            raise V3GenerationError("generation replay prompt hash drifted")
        response = checkpoint.get("response")
        if not isinstance(response, Mapping):
            raise V3GenerationError("generation replay response is absent")
        try:
            result = GenerationResponse(
                expression=response["candidate_expression"],
                input_tokens=response["input_tokens"],
                output_tokens=response["output_tokens"],
                latency_ms=float(response["latency_ms"]),
                provider_request_count=response["accepted_provider_request_count"],
                seed_supported=response["seed_supported"],
                provider_model=response["provider_model"],
                finish_reason=response["finish_reason"],
                prompt_cache_hit_tokens=response["prompt_cache_hit_tokens"],
                prompt_cache_miss_tokens=response["prompt_cache_miss_tokens"],
                reasoning_tokens=response["reasoning_tokens"],
                candidate_format=response["candidate_format"],
                provider_fingerprint=None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise V3GenerationError("generation replay response is malformed") from exc
        self.calls += 1
        return result


def generation_world_view(plan_entry: Mapping[str, Any]) -> GenerationWorldView:
    """Regenerate and reduce one manifest-bound world before model generation."""

    if not isinstance(plan_entry, Mapping):
        raise TypeError("plan_entry must be an object")
    try:
        seed = plan_entry["world_seed"]
        depth = plan_entry["depth"]
        expected_hash = plan_entry["world_hash"]
    except KeyError as exc:
        raise V3GenerationError("plan entry lacks world coordinates") from exc
    if type(seed) is not int or type(depth) is not int:
        raise V3GenerationError("plan entry world coordinates are not integers")
    full_world = generate_world(seed, depth=depth)
    if full_world.world_hash != expected_hash:
        raise V3GenerationError("regenerated world hash drifted from execution plan")
    view = GenerationWorldView(
        seed=full_world.seed,
        train=tuple(full_world.train),
        probe=tuple(full_world.probe),
        depth_tier=full_world.depth_tier,
        world_hash=full_world.world_hash,
        domain=tuple(full_world.domain),
    )
    # Do not retain the object carrying test labels or the hidden law on the
    # generation path.  World construction itself is deterministic local code,
    # so this is an interface boundary rather than OS-level capability isolation.
    del full_world
    return view


def _stratum(
    manifest: Mapping[str, Any], model_stratum: str
) -> dict[str, Any]:
    frozen = manifest["frozen_config"]
    matches = [
        item
        for item in frozen["model_strata"]
        if item["stratum_id"] == model_stratum
    ]
    if len(matches) != 1:
        raise V3GenerationError("plan model stratum is absent or duplicated")
    return dict(matches[0])


def accepted_response_contract(
    manifest: Mapping[str, Any], model_stratum: str
) -> AcceptedResponseContract:
    """Construct the exact live contract from a validated frozen manifest."""

    validated = validate_campaign_manifest(manifest)
    stored = _stratum(validated, model_stratum)["route_contract"][
        "accepted_response_contract"
    ]
    fingerprint_hash = stored["provider_fingerprint_sha256"]
    if stored["provider_fingerprint_mode"] == "absent":
        fingerprint_hash = None
    return AcceptedResponseContract(
        provider_models=tuple(stored["provider_models"]),
        finish_reasons=tuple(stored["finish_reasons"]),
        max_output_tokens=stored["max_output_tokens"],
        seed_supported=stored["seed_supported"],
        require_zero_reasoning_tokens=stored["require_zero_reasoning_tokens"],
        prompt_cache_mode=stored["prompt_cache_mode"],
        provider_fingerprint_mode=stored["provider_fingerprint_mode"],
        provider_fingerprint_sha256=fingerprint_hash,
    )


def bind_live_generator(
    manifest: Mapping[str, Any],
    plan_entry: Mapping[str, Any],
    generator: OpenAICompatibleGenerator,
) -> tuple[AcceptedResponseContract, FrozenTransactionIdentity]:
    """Fail before network on any route, plan, or transport-profile drift."""

    validated = validate_campaign_manifest(manifest)
    if type(generator) is not OpenAICompatibleGenerator:
        raise TypeError("live V3 generator must be the exact audited adapter type")
    try:
        index = plan_entry["shard_index"]
    except (KeyError, TypeError) as exc:
        raise V3GenerationError("plan entry lacks a shard index") from exc
    plan = validated["execution_plan"]
    if type(index) is not int or not 0 <= index < len(plan) or plan[index] != plan_entry:
        raise V3GenerationError("plan entry is not bound to the campaign manifest")
    stratum = _stratum(validated, plan_entry["model_stratum"])
    frozen_request = stratum["route_contract"]["sanitized_request_contract"]
    runtime_request = generator.sanitized_request_contract()
    if runtime_request != frozen_request:
        raise V3GenerationError("runtime generator request route drifted")
    if runtime_request.get("transport_profile") != "stdlib-urllib-one-shot-v1":
        raise V3GenerationError("live V3 requires the audited one-shot transport")
    contract = accepted_response_contract(validated, plan_entry["model_stratum"])
    binding = route_binding_sha256(generator, contract)
    if binding != stratum["route_contract"]["route_binding_sha256"]:
        raise V3GenerationError("runtime route binding drifted from frozen stratum")
    if binding != plan_entry["route_binding_sha256"]:
        raise V3GenerationError("runtime route binding drifted from plan entry")
    try:
        identity = FrozenTransactionIdentity(
            **transaction_identity_payload(validated, plan_entry)
        )
    except (TypeError, ValueError, V3DevelopmentError) as exc:
        raise V3GenerationError("cannot derive frozen shard transaction") from exc
    return contract, identity


def policy_for_entry(
    manifest: Mapping[str, Any], plan_entry: Mapping[str, Any]
) -> Any:
    """Build a fresh policy instance from the exact arm specification."""

    validated = validate_campaign_manifest(manifest)
    arm_id = plan_entry.get("arm_id")
    if arm_id not in validated["frozen_config"]["arms"]:
        raise V3GenerationError("plan entry arm is not in the frozen config")
    return _policy_from_config(
        str(arm_id), validated["frozen_config"]["arms"][str(arm_id)]
    )


def _records(result: EpisodeResult) -> list[Any]:
    return [record for round_records in result.rounds for record in round_records]


def generation_state_sha256(result: EpisodeResult) -> str:
    """Digest the exact selection-relevant replay state without publishing it."""

    records = _records(result)
    selected = result.final_candidate
    return sha256_json(
        {
            "candidates": [
                {
                    "round_index": record.round_index,
                    "candidate_index": record.candidate_index,
                    "temperature": float(record.temperature),
                    "probe_score": float(record.probe_score),
                    "syntax_valid": bool(record.syntax_valid),
                    "runtime_valid": bool(record.runtime_valid),
                    "failure_codes": list(record.failure_codes),
                    "node_count": int(record.node_count),
                    "canonical_hash": str(record.canonical_hash),
                    "behavior_hash": str(record.behavior_hash),
                }
                for record in records
            ],
            "selected": (
                None
                if selected is None
                else {
                    "probe_score": float(selected.probe_score),
                    "node_count": int(selected.node_count),
                    "canonical_hash": str(selected.canonical_hash),
                    "behavior_hash": str(selected.behavior_hash),
                }
            ),
            "temperature_trajectory": [float(item) for item in result.temperatures],
            "slot_temperature_trajectory": [
                [float(item) for item in row] for row in result.slot_temperatures
            ],
            "controller_trace": _sanitized_controller_trace(result),
        }
    )


def episode_metrics(
    result: EpisodeResult, plan_entry: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the closed, non-candidate-linked metrics allowed in a seal."""

    if not isinstance(result, EpisodeResult):
        raise TypeError("result must be an EpisodeResult")
    if result.final_test is not None:
        raise V3GenerationError("generation episode cannot contain private-test state")
    if result.candidate_count != 20 or len(result.rounds) != 5 or any(
        len(items) != 4 for items in result.rounds
    ):
        raise V3GenerationError("generation episode did not complete exact 5x4 budget")
    if getattr(result.world, "world_hash", None) != plan_entry.get("world_hash"):
        raise V3GenerationError("episode world drifted from plan entry")
    for round_index, items in enumerate(result.rounds):
        for candidate_index, record in enumerate(items):
            if (record.round_index, record.candidate_index) != (
                round_index,
                candidate_index,
            ):
                raise V3GenerationError("episode logical-slot order drifted")
    records = _records(result)
    valid = [
        record
        for record in records
        if record.syntax_valid and record.runtime_valid
    ]
    canonical_unique = {record.canonical_hash for record in valid if record.canonical_hash}
    behavioral_unique = {record.behavior_hash for record in valid if record.behavior_hash}
    format_counts: dict[str, int] = {}
    for record in records:
        key = "unavailable" if record.candidate_format is None else record.candidate_format
        format_counts[key] = format_counts.get(key, 0) + 1
    trace = _sanitized_controller_trace(result)
    if plan_entry.get("arm_id") == "E2":
        if len(trace) != 5:
            raise V3GenerationError("E2 episode must contain exactly five trace rows")
    elif trace:
        raise V3GenerationError("non-E2 episode cannot contain an E2 controller trace")
    selected = result.final_candidate
    if selected is None and valid:
        raise V3GenerationError("valid candidates exist but selection is absent")
    return {
        "planned_candidate_count": 20,
        "completed_candidate_count": 20,
        "outer_schema_valid_count": sum(
            record.candidate_format == "json_expression" for record in records
        ),
        "search_valid_count": len(valid),
        "canonical_unique_count": len(canonical_unique),
        "behavioral_unique_count": len(behavioral_unique),
        "all_invalid": not valid,
        "selection_exists": selected is not None,
        "selected_probe_score": (
            None if selected is None else float(selected.probe_score)
        ),
        "round_best_scores": _round_best_scores(result),
        "failure_counts": _failure_counts(result),
        "candidate_format_counts": dict(sorted(format_counts.items())),
        "temperature_trajectory": [float(item) for item in result.temperatures],
        "slot_temperature_trajectory": [
            [float(item) for item in row] for row in result.slot_temperatures
        ],
        "controller_trace": trace,
        "generation_state_sha256": generation_state_sha256(result),
        "private_test_evaluated": False,
    }


def stable_execution_audit(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one complete shard audit to replay-invariant seal fields.

    ``logical_calls_seen`` and ``call_checkpoint_replays`` describe the current
    process invocation rather than the durable scientific state, so neither is
    allowed into an episode seal.
    """

    if not isinstance(audit, Mapping):
        raise TypeError("audit must be an object")
    missing = [field for field in _STABLE_EXECUTION_AUDIT_FIELDS if field not in audit]
    if missing:
        raise V3GenerationError(
            f"execution audit lacks stable field {missing[0]}"
        )
    result = {field: audit[field] for field in _STABLE_EXECUTION_AUDIT_FIELDS}
    if (
        result["durable_logical_call_checkpoints"] != 20
        or result["shard_complete"] is not True
        or result["content_retry_count"] != 0
    ):
        raise V3GenerationError("execution audit is not a complete 20-call shard")
    for field in (
        "unresolved_slot_count",
        "exhausted_slot_count",
        "fatal_slot_count",
        "ready_for_retry_slot_count",
    ):
        if result[field] != 0:
            raise V3GenerationError(
                f"execution audit contains unsafe terminal state {field}"
            )
    ordinals = result["accepted_attempt_ordinals"]
    if (
        not isinstance(ordinals, list)
        or len(ordinals) != 20
        or any(type(value) is not int or not 1 <= value <= 3 for value in ordinals)
    ):
        raise V3GenerationError("execution audit accepted-attempt ledger drifted")
    physical_starts = result["physical_request_starts"]
    retry_count = result["retry_count"]
    if (
        type(physical_starts) is not int
        or not 20 <= physical_starts <= 60
        or type(retry_count) is not int
        or retry_count != physical_starts - 20
    ):
        raise V3GenerationError("execution audit physical-attempt accounting drifted")
    return result


def replay_committed_generation(
    manifest: Mapping[str, Any],
    plan_entry: Mapping[str, Any],
    checkpoints: Sequence[Mapping[str, Any]],
) -> EpisodeResult:
    """Deterministically reconstruct selection state from committed calls."""

    validated = validate_campaign_manifest(manifest)
    shard = plan_entry.get("shard_index") if isinstance(plan_entry, Mapping) else None
    if type(shard) is not int or not 0 <= shard < len(validated["execution_plan"]):
        raise V3GenerationError("generation replay plan entry lacks a shard index")
    if validated["execution_plan"][shard] != plan_entry:
        raise V3GenerationError("generation replay plan entry is not manifest-bound")
    episode = validated["frozen_config"]["episode"]
    replay = _CommittedCheckpointReplayGenerator(checkpoints)
    result = run_episode(
        generation_world_view(plan_entry),
        replay,
        verifier=Verifier(
            counterexample_limit=episode["max_counterexamples_per_round"]
        ),
        policy=policy_for_entry(validated, plan_entry),
        rounds=episode["rounds"],
        candidates_per_round=episode["candidates_per_round"],
        archive_capacity=episode["archive_size"],
        max_counterexamples=(
            episode["rounds"] * episode["max_counterexamples_per_round"]
        ),
        max_output_tokens=episode["max_output_tokens"],
        max_counterexamples_per_round=episode["max_counterexamples_per_round"],
        seed=plan_entry["sampling_base_seed"],
        evaluate_test=False,
    )
    if replay.calls != 20 or result.candidate_count != 20 or result.final_test is not None:
        raise V3GenerationError("generation replay did not close the exact budget")
    return result


def run_next_v3_generation_shard(
    campaign_dir: str | Path,
    generator: OpenAICompatibleGenerator,
    *,
    current_source_manifest: Mapping[str, Any] | None = None,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Run, resume, and seal the sole campaign-frontier generation episode.

    The return value intentionally contains no per-episode metric.  Gate and
    main outcomes remain sealed until the appropriate aggregate artifact is
    available, avoiding an accidental outcome-bearing interim look.
    """

    # Imported lazily so the pure generation helpers remain usable while a new
    # campaign directory is being prepared and to keep the storage layer free
    # to validate E2 traces through this module without an import cycle.
    from .v3_campaign import (
        acquire_campaign_frontier,
        acquire_campaign_lock,
        compatibility_screen_path,
        generation_barrier_path,
        load_campaign_manifest,
        load_compatibility_screen,
        load_generation_barrier,
        next_shard_frontier,
        publish_compatibility_screen,
        publish_episode_seal,
        publish_generation_barrier,
    )

    root = Path(campaign_dir)
    current = (
        source_manifest(PROJECT_ROOT)
        if current_source_manifest is None
        else current_source_manifest
    )
    manifest_envelope = load_campaign_manifest(
        root, current_source_manifest=current
    )
    manifest = manifest_envelope["payload"]

    # Repair only control artifacts whose data were already durably sealed.
    # This covers a crash after gate shard 7 or main shard 103 without issuing
    # a new model request.
    with acquire_campaign_lock(root, blocking=False) as lease:
        frontier = next_shard_frontier(root, manifest_envelope, lease=lease)
        if frontier == 8 and not compatibility_screen_path(root).exists():
            publish_compatibility_screen(root, manifest_envelope, lease=lease)
        if frontier is None and not generation_barrier_path(root).exists():
            publish_generation_barrier(root, manifest_envelope, lease=lease)

    if generation_barrier_path(root).exists():
        load_generation_barrier(root, manifest_envelope)
        return {
            "status": "generation_complete",
            "sealed_episode_count": 104,
            "next_shard_index": None,
            "private_test_evaluated": False,
        }

    if compatibility_screen_path(root).exists():
        screen = load_compatibility_screen(root, manifest_envelope)["payload"]
        if screen["status"] != "passed":
            return {
                "status": "compatibility_screen_failed",
                "sealed_episode_count": 8,
                "next_shard_index": None,
                "private_test_evaluated": False,
            }

    frontier = next_shard_frontier(root, manifest_envelope)
    if frontier is None:
        raise V3GenerationError("generation frontier closed without a barrier")
    entry = manifest["execution_plan"][frontier]
    contract, identity = bind_live_generator(manifest, entry, generator)
    episode = manifest["frozen_config"]["episode"]
    logical_calls = episode["rounds"] * episode["candidates_per_round"]
    if logical_calls != 20 or entry["logical_calls"] != logical_calls:
        raise V3GenerationError("manifest episode budget drifted from exact 5x4")

    with acquire_campaign_frontier(root, manifest_envelope, frontier) as lease:
        # Rebind after taking the global lease so route mutation cannot race the
        # preflight that authorizes this episode.
        contract, identity = bind_live_generator(manifest, entry, generator)
        world = generation_world_view(entry)
        policy = policy_for_entry(manifest, entry)
        durable = DurableLogicalSlotGenerator(
            campaign_dir=root,
            shard_index=frontier,
            generator=generator,
            frozen_route_binding_sha256=entry["route_binding_sha256"],
            transaction_identity=identity,
            response_contract=contract,
            forbidden_values=forbidden_values,
        )
        with durable:
            result = run_episode(
                world,
                durable,
                verifier=Verifier(
                    counterexample_limit=episode["max_counterexamples_per_round"]
                ),
                policy=policy,
                rounds=episode["rounds"],
                candidates_per_round=episode["candidates_per_round"],
                archive_capacity=episode["archive_size"],
                max_counterexamples=(
                    episode["rounds"]
                    * episode["max_counterexamples_per_round"]
                ),
                max_output_tokens=episode["max_output_tokens"],
                max_counterexamples_per_round=episode[
                    "max_counterexamples_per_round"
                ],
                seed=entry["sampling_base_seed"],
                evaluate_test=False,
            )
            metrics = episode_metrics(result, entry)
            audit = stable_execution_audit(durable.execution_audit())
            postflight = (
                source_manifest(PROJECT_ROOT)
                if current_source_manifest is None
                else current_source_manifest
            )
            # A drift leaves recoverable call checkpoints but cannot mint a
            # scientific seal. Restoring the frozen tree permits offline replay.
            if load_campaign_manifest(
                root, current_source_manifest=postflight
            )["payload_sha256"] != manifest_envelope["payload_sha256"]:
                raise V3GenerationError("campaign manifest changed during episode")
            publish_episode_seal(
                root,
                manifest_envelope,
                entry,
                episode_metrics=metrics,
                execution_audit=audit,
                forbidden_values=forbidden_values,
                lease=lease,
            )

        if frontier == 7:
            screen = publish_compatibility_screen(
                root, manifest_envelope, lease=lease
            )["payload"]
            status = (
                "compatibility_screen_passed"
                if screen["status"] == "passed"
                else "compatibility_screen_failed"
            )
        elif frontier == 103:
            publish_generation_barrier(root, manifest_envelope, lease=lease)
            status = "generation_complete"
        else:
            status = "gate_in_progress" if frontier < 7 else "main_in_progress"

    next_shard_index = (
        None
        if frontier == 103 or status == "compatibility_screen_failed"
        else frontier + 1
    )
    return {
        "status": status,
        "sealed_episode_count": frontier + 1,
        "next_shard_index": next_shard_index,
        "private_test_evaluated": False,
    }


__all__ = [
    "GenerationWorldView",
    "V3GenerationError",
    "accepted_response_contract",
    "bind_live_generator",
    "episode_metrics",
    "generation_state_sha256",
    "generation_world_view",
    "policy_for_entry",
    "replay_committed_generation",
    "run_next_v3_generation_shard",
    "stable_execution_audit",
]
