"""V3 logical-slot coordinator with identical-request physical retries.

This module is intentionally separate from :mod:`src.staged_pilot`: the sealed
v2 campaign used one request per logical call and whole-shard recovery, whereas
v3 has a prospectively different operational estimand.  The wrapper below is
consumed by the unchanged episode runner.  On resume, the runner deterministically
recreates prompts and policy state from slot zero; committed call checkpoints
are replayed and only the first missing slot can reach the network.

No campaign CLI is exposed yet.  A route must first pass a canary and bind the
template's provider/model/source fields before this coordinator is eligible for
live use.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from .dsl import parse_sexpr, to_sexpr
from .pilot_checkpoint import (
    INVALID_CANDIDATE_SENTINEL,
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
)
from .pilot_checkpoint_v3 import (
    MAX_PHYSICAL_ATTEMPTS,
    SLOTS_PER_SHARD,
    V3_COORDINATOR_VERSION,
    PilotCheckpointV3Error,
    attempt_outcome_path,
    acquire_shard_lock,
    inspect_shard_prefix,
    inspect_slot_state,
    load_attempt_outcome,
    load_attempt_start,
    load_call_checkpoint,
    publish_attempt_outcome,
    publish_attempt_start,
    publish_call_checkpoint,
    validate_artifact_inventory,
)
from .providers.openai_compatible import (
    HTTPStatusError,
    OpenAICompatibleGenerator,
    PreparedRequest,
    ResponsePayloadError,
    TransportError,
    UsagePayloadError,
)
from .runner import CANDIDATE_FORMATS, GenerationResponse


V3_ACCEPTED_ATTEMPT_ESTIMAND = "first_durably_recorded_http_success"
V3_CANDIDATES_PER_ROUND = 4


class StagedPilotV3Error(RuntimeError):
    """Base class for safe v3 orchestration failures."""


class V3SlotUnresolvedError(StagedPilotV3Error):
    """A physical start lacks a durable outcome and cannot be retried safely."""


class V3TransportExhaustedError(StagedPilotV3Error):
    """All three physical attempts ended in a frozen retryable class."""


class V3SlotFatalError(StagedPilotV3Error):
    """A slot already has a durable fatal outcome."""


class V3ResponseContractError(StagedPilotV3Error):
    """A 2xx response violated a frozen provider contract."""

    _CATEGORIES = frozenset(
        {
            "provider_model_contract",
            "finish_reason_contract",
            "output_cap_contract",
            "cache_telemetry_contract",
            "provider_fingerprint_contract",
        }
    )

    def __init__(self, category: str) -> None:
        if category not in self._CATEGORIES:
            raise ValueError("response contract category is not in the closed set")
        self.category = category
        super().__init__(f"accepted HTTP response violated {category}")


def _frozen_sha256(name: str, value: Any) -> str:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _frozen_label(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{name} must be a non-empty normalized string")
    if any(character in value for character in ("/", "\\", "\x00")):
        raise ValueError(f"{name} cannot contain a path separator")
    return value


@dataclass(frozen=True)
class FrozenTransactionIdentity:
    """Public coordinates binding one shard to a verified frozen campaign.

    The campaign coordinator constructs this value from a hash-verified
    campaign manifest and its exact execution-plan entry.  Slot checkpoints
    persist only :attr:`binding_sha256`, so they cannot be transplanted into a
    different campaign, plan, route stratum, world, or arm without detection.
    """

    campaign_manifest_payload_sha256: str
    execution_plan_sha256: str
    plan_entry_sha256: str
    run_id: str
    shard_index: int
    model_stratum: str
    phase: str
    world_seed: int
    depth: int
    arm_id: str

    def __post_init__(self) -> None:
        for name in (
            "campaign_manifest_payload_sha256",
            "execution_plan_sha256",
            "plan_entry_sha256",
            "run_id",
        ):
            _frozen_sha256(name, getattr(self, name))
        if type(self.shard_index) is not int or not 0 <= self.shard_index < 104:
            raise ValueError("shard_index must be an integer in [0, 103]")
        _frozen_label("model_stratum", self.model_stratum)
        if self.phase not in {"gate", "main"}:
            raise ValueError("phase must be 'gate' or 'main'")
        if type(self.world_seed) is not int or self.world_seed < 0:
            raise ValueError("world_seed must be a non-negative integer")
        if type(self.depth) is not int or self.depth < 1:
            raise ValueError("depth must be a positive integer")
        if self.arm_id not in {"L", "H", "C", "E2"}:
            raise ValueError("arm_id is not in the frozen v3 arm set")

    @classmethod
    def from_plan_entry(
        cls,
        *,
        campaign_manifest_payload_sha256: str,
        execution_plan_sha256: str,
        entry: Mapping[str, Any],
    ) -> FrozenTransactionIdentity:
        """Derive, rather than accept, the entry hash and public coordinates."""

        if not isinstance(entry, Mapping):
            raise TypeError("execution-plan entry must be an object")
        try:
            detached = json.loads(canonical_json_bytes(entry).decode("utf-8"))
            stored_entry_hash = detached["plan_entry_sha256"]
            entry_basis = dict(detached)
            del entry_basis["plan_entry_sha256"]
            if stored_entry_hash != sha256_json(entry_basis):
                raise ValueError("execution-plan entry self-hash drifted")
            return cls(
                campaign_manifest_payload_sha256=campaign_manifest_payload_sha256,
                execution_plan_sha256=execution_plan_sha256,
                plan_entry_sha256=stored_entry_hash,
                run_id=detached["run_id"],
                shard_index=detached["shard_index"],
                model_stratum=detached["model_stratum"],
                phase=detached["phase"],
                world_seed=detached["world_seed"],
                depth=detached["depth"],
                arm_id=detached["arm_id"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("execution-plan entry lacks frozen transaction fields") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_manifest_payload_sha256": self.campaign_manifest_payload_sha256,
            "execution_plan_sha256": self.execution_plan_sha256,
            "plan_entry_sha256": self.plan_entry_sha256,
            "run_id": self.run_id,
            "shard_index": self.shard_index,
            "model_stratum": self.model_stratum,
            "phase": self.phase,
            "world_seed": self.world_seed,
            "depth": self.depth,
            "arm_id": self.arm_id,
        }

    @property
    def binding_sha256(self) -> str:
        return sha256_json(
            {
                "kind": "v3-frozen-shard-transaction",
                "coordinator_version": V3_COORDINATOR_VERSION,
                "identity": self.to_dict(),
            }
        )


@dataclass(frozen=True)
class AcceptedResponseContract:
    """Sanitized route contract frozen after a provider canary."""

    provider_models: tuple[str, ...]
    finish_reasons: tuple[str, ...] = ("stop",)
    max_output_tokens: int = 256
    seed_supported: bool | None = None
    require_zero_reasoning_tokens: bool = False
    prompt_cache_mode: str = "absent"
    provider_fingerprint_mode: str = "absent"
    provider_fingerprint_sha256: str | None = None

    def __post_init__(self) -> None:
        if type(self.provider_models) is not tuple or not self.provider_models or any(
            not isinstance(value, str) or not value.strip()
            for value in self.provider_models
        ):
            raise ValueError("provider_models must be a tuple of frozen aliases")
        if type(self.finish_reasons) is not tuple or not self.finish_reasons or any(
            not isinstance(value, str) or not value.strip()
            for value in self.finish_reasons
        ):
            raise ValueError("finish_reasons must be a tuple of non-empty values")
        if type(self.max_output_tokens) is not int or self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be a positive integer")
        if self.seed_supported is not None and type(self.seed_supported) is not bool:
            raise TypeError("seed_supported must be bool or None")
        if type(self.require_zero_reasoning_tokens) is not bool:
            raise TypeError("require_zero_reasoning_tokens must be a boolean")
        if self.prompt_cache_mode not in {"absent", "complete"}:
            raise ValueError("prompt_cache_mode must be 'absent' or 'complete'")
        if self.provider_fingerprint_mode not in {"absent", "exact_sha256"}:
            raise ValueError(
                "provider_fingerprint_mode must be 'absent' or 'exact_sha256'"
            )
        if self.provider_fingerprint_mode == "absent":
            if self.provider_fingerprint_sha256 is not None:
                raise ValueError("absent fingerprint mode requires a null hash")
        elif not (
            isinstance(self.provider_fingerprint_sha256, str)
            and len(self.provider_fingerprint_sha256) == 64
            and all(
                character in "0123456789abcdef"
                for character in self.provider_fingerprint_sha256
            )
        ):
            raise ValueError("exact fingerprint mode requires a lowercase SHA-256")

    def validate(self, response: GenerationResponse) -> None:
        if response.provider_request_count != 1:
            raise V3ResponseContractError("provider_model_contract")
        if response.provider_model not in self.provider_models:
            raise V3ResponseContractError("provider_model_contract")
        if response.finish_reason not in self.finish_reasons:
            raise V3ResponseContractError("finish_reason_contract")
        if response.output_tokens > self.max_output_tokens:
            raise V3ResponseContractError("output_cap_contract")
        if response.seed_supported is not self.seed_supported:
            raise V3ResponseContractError("provider_model_contract")
        if response.candidate_format not in CANDIDATE_FORMATS:
            raise V3ResponseContractError("provider_model_contract")
        if self.require_zero_reasoning_tokens and response.reasoning_tokens not in {
            None,
            0,
        }:
            raise V3ResponseContractError("provider_model_contract")
        cache_values = (
            response.prompt_cache_hit_tokens,
            response.prompt_cache_miss_tokens,
        )
        if self.prompt_cache_mode == "absent" and cache_values != (None, None):
            raise V3ResponseContractError("cache_telemetry_contract")
        if self.prompt_cache_mode == "complete" and any(
            value is None for value in cache_values
        ):
            raise V3ResponseContractError("cache_telemetry_contract")
        if self.prompt_cache_mode == "complete" and (
            response.input_tokens != sum(int(value) for value in cache_values)
        ):
            raise V3ResponseContractError("cache_telemetry_contract")
        if self.provider_fingerprint_mode == "absent":
            if response.provider_fingerprint is not None:
                raise V3ResponseContractError("provider_fingerprint_contract")
        else:
            observed = response.provider_fingerprint
            if not isinstance(observed, str) or (
                hashlib.sha256(observed.encode("utf-8")).hexdigest()
                != self.provider_fingerprint_sha256
            ):
                raise V3ResponseContractError("provider_fingerprint_contract")

    def validate_checkpoint_payload(self, payload: Mapping[str, Any]) -> None:
        """Validate a normalized secret-free response during checkpoint replay."""

        response = _generation_response(payload)
        without_fingerprint = AcceptedResponseContract(
            provider_models=self.provider_models,
            finish_reasons=self.finish_reasons,
            max_output_tokens=self.max_output_tokens,
            seed_supported=self.seed_supported,
            require_zero_reasoning_tokens=self.require_zero_reasoning_tokens,
            prompt_cache_mode=self.prompt_cache_mode,
            provider_fingerprint_mode="absent",
        )
        without_fingerprint.validate(response)
        observed_hash = payload.get("provider_fingerprint_sha256")
        if self.provider_fingerprint_mode == "absent":
            if observed_hash is not None:
                raise V3ResponseContractError("provider_fingerprint_contract")
        elif observed_hash != self.provider_fingerprint_sha256:
            raise V3ResponseContractError("provider_fingerprint_contract")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_models": list(self.provider_models),
            "finish_reasons": list(self.finish_reasons),
            "max_output_tokens": self.max_output_tokens,
            "seed_supported": self.seed_supported,
            "require_zero_reasoning_tokens": self.require_zero_reasoning_tokens,
            "prompt_cache_mode": self.prompt_cache_mode,
            "provider_fingerprint_mode": self.provider_fingerprint_mode,
            "provider_fingerprint_sha256": self.provider_fingerprint_sha256,
        }


def route_binding_sha256(
    generator: OpenAICompatibleGenerator,
    response_contract: AcceptedResponseContract,
) -> str:
    """Derive the route binding; callers cannot substitute an opaque hash."""

    if type(generator) is not OpenAICompatibleGenerator:
        raise TypeError("generator must be an OpenAICompatibleGenerator")
    if type(response_contract) is not AcceptedResponseContract:
        raise TypeError("response_contract must be an AcceptedResponseContract")
    return sha256_json(
        {
            "coordinator_version": V3_COORDINATOR_VERSION,
            "accepted_attempt_estimand": V3_ACCEPTED_ATTEMPT_ESTIMAND,
            "request_contract": generator.sanitized_request_contract(),
            "response_contract": response_contract.to_dict(),
        }
    )


def _checkpoint_response(response: GenerationResponse) -> dict[str, Any]:
    """Normalize a 2xx assistant result without preserving malformed text."""

    expression = response.expression
    parse_status = "invalid_candidate"
    canonical = INVALID_CANDIDATE_SENTINEL
    if response.candidate_format == "json_expression" and isinstance(expression, str):
        try:
            canonical = to_sexpr(parse_sexpr(expression))
            parse_status = "canonical_dsl"
        except Exception:
            # Invalid DSL is an accepted scientific content result.  Its raw
            # text is intentionally discarded and is never a retry trigger.
            canonical = INVALID_CANDIDATE_SENTINEL
    return {
        "candidate_expression": canonical,
        "candidate_parse_status": parse_status,
        "candidate_format": response.candidate_format,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_ms": float(response.latency_ms),
        "accepted_provider_request_count": 1,
        "seed_supported": response.seed_supported,
        "provider_model": response.provider_model,
        "finish_reason": response.finish_reason,
        "prompt_cache_hit_tokens": response.prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": response.prompt_cache_miss_tokens,
        "reasoning_tokens": response.reasoning_tokens,
        "provider_fingerprint_sha256": (
            None
            if response.provider_fingerprint is None
            else hashlib.sha256(
                response.provider_fingerprint.encode("utf-8")
            ).hexdigest()
        ),
    }


def _generation_response(payload: Mapping[str, Any]) -> GenerationResponse:
    return GenerationResponse(
        expression=payload["candidate_expression"],
        input_tokens=payload["input_tokens"],
        output_tokens=payload["output_tokens"],
        latency_ms=float(payload["latency_ms"]),
        provider_request_count=payload["accepted_provider_request_count"],
        seed_supported=payload["seed_supported"],
        provider_model=payload["provider_model"],
        finish_reason=payload["finish_reason"],
        prompt_cache_hit_tokens=payload["prompt_cache_hit_tokens"],
        prompt_cache_miss_tokens=payload["prompt_cache_miss_tokens"],
        reasoning_tokens=payload["reasoning_tokens"],
        candidate_format=payload["candidate_format"],
        provider_fingerprint=None,
    )


class DurableLogicalSlotGenerator:
    """Make an OpenAI-compatible generator resumable at logical-slot granularity.

    One instance is scoped to one 20-call episode/shard and must be used while
    holding :func:`src.pilot_checkpoint_v3.acquire_shard_lock`.  It deliberately
    has the runner's ordinary ``generate`` surface so policy, archive, prompt,
    and verifier code remain shared with the historical implementation.
    """

    def __init__(
        self,
        *,
        campaign_dir: str | Path,
        shard_index: int,
        generator: OpenAICompatibleGenerator,
        frozen_route_binding_sha256: str,
        transaction_identity: FrozenTransactionIdentity,
        response_contract: AcceptedResponseContract,
        forbidden_values: Sequence[str] = (),
    ) -> None:
        if type(generator) is not OpenAICompatibleGenerator:
            raise TypeError("v3 requires an OpenAICompatibleGenerator")
        if generator.timeout != 120.0:
            raise ValueError("v3 generator timeout must equal the frozen 120 seconds")
        if type(shard_index) is not int or shard_index < 0:
            raise ValueError("shard_index must be a non-negative integer")
        if type(transaction_identity) is not FrozenTransactionIdentity:
            raise TypeError("transaction_identity must be frozen plan-derived identity")
        if type(response_contract) is not AcceptedResponseContract:
            raise TypeError("response_contract must be an AcceptedResponseContract")
        if response_contract.max_output_tokens != 256:
            raise ValueError("v3 response contract must freeze max_output_tokens=256")
        if transaction_identity.shard_index != shard_index:
            raise ValueError("transaction identity shard does not match coordinator")
        if len(frozen_route_binding_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in frozen_route_binding_sha256
        ):
            raise ValueError("frozen_route_binding_sha256 must be a lowercase SHA-256")
        computed_route_binding = route_binding_sha256(generator, response_contract)
        if frozen_route_binding_sha256 != computed_route_binding:
            raise ValueError("runtime generator/response contract drifted from frozen route")
        self.campaign_dir = Path(campaign_dir)
        self.shard_index = shard_index
        self.generator = generator
        self.route_binding_sha256 = computed_route_binding
        self.transaction_identity = transaction_identity
        self.transaction_binding_sha256 = transaction_identity.binding_sha256
        self.response_contract = response_contract
        if any(not isinstance(value, str) or not value for value in forbidden_values):
            raise ValueError("forbidden_values must contain only non-empty strings")
        self.forbidden_values = tuple(
            dict.fromkeys(
                (*generator._persistence_forbidden_values(), *forbidden_values)
            )
        )
        self.slots_per_shard = SLOTS_PER_SHARD
        self.candidates_per_round = V3_CANDIDATES_PER_ROUND
        self.logical_calls_seen = 0
        self.call_checkpoint_replays = 0
        self._accepted: list[tuple[int, GenerationResponse]] = []
        self._lock_context: Any | None = None
        self._owner_thread_id: int | None = None
        self._lifecycle_mutex = threading.Lock()

    def __enter__(self) -> DurableLogicalSlotGenerator:
        with self._lifecycle_mutex:
            if self._lock_context is not None:
                raise StagedPilotV3Error("v3 shard generator lock is already held")
            context = acquire_shard_lock(
                self.campaign_dir,
                self.shard_index,
                blocking=False,
            )
            context.__enter__()
            self._lock_context = context
            self._owner_thread_id = threading.get_ident()
        try:
            validate_artifact_inventory(self.campaign_dir)
            inspect_shard_prefix(self.campaign_dir, self.shard_index)
        except BaseException as exc:
            self._lock_context = None
            self._owner_thread_id = None
            context.__exit__(type(exc), exc, exc.__traceback__)
            raise
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        context = self._lock_context
        if context is None:
            raise StagedPilotV3Error("v3 shard generator lock is not held")
        if self._owner_thread_id != threading.get_ident():
            raise StagedPilotV3Error(
                "v3 shard generator context is single-threaded"
            )
        self._lock_context = None
        self._owner_thread_id = None
        suppressed = context.__exit__(exc_type, exc, traceback)
        if exc_type is None and self.logical_calls_seen != self.slots_per_shard:
            raise StagedPilotV3Error(
                "cannot cleanly leave a partially replayed/generated shard"
            )
        return suppressed

    def _assert_runtime_route_unchanged(self) -> None:
        if route_binding_sha256(self.generator, self.response_contract) != self.route_binding_sha256:
            raise PilotCheckpointV3Error("runtime provider route changed after preflight")

    def _verify_prepared_binding(
        self,
        *,
        slot_index: int,
        prepared: PreparedRequest,
        prompt_sha256: str,
    ) -> None:
        state = inspect_slot_state(self.campaign_dir, self.shard_index, slot_index)
        for ordinal in state.started_attempts:
            payload = load_attempt_start(
                self.campaign_dir,
                self.shard_index,
                slot_index,
                ordinal,
            )["payload"]
            if payload["request_body_sha256"] != prepared.body_sha256:
                raise PilotCheckpointV3Error("replayed prepared request hash drifted")
            if payload["prompt_sha256"] != prompt_sha256:
                raise PilotCheckpointV3Error("replayed prompt hash drifted")
            if payload["route_binding_sha256"] != self.route_binding_sha256:
                raise PilotCheckpointV3Error("replayed provider route binding drifted")
            if (
                payload["transaction_binding_sha256"]
                != self.transaction_binding_sha256
            ):
                raise PilotCheckpointV3Error("replayed campaign transaction drifted")

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
        slot_index = self.logical_calls_seen
        if self._lock_context is None:
            raise StagedPilotV3Error(
                "v3 generation requires the shard generator context lock"
            )
        if self._owner_thread_id != threading.get_ident():
            raise StagedPilotV3Error(
                "v3 shard generation must stay on its owning thread"
            )
        shard_recovery = inspect_shard_prefix(self.campaign_dir, self.shard_index)
        if slot_index < shard_recovery.committed_prefix_count:
            expected_slot_status = "committed"
        elif slot_index == shard_recovery.next_slot_index:
            expected_slot_status = shard_recovery.active_status
        else:
            raise PilotCheckpointV3Error("runner skipped the shard recovery frontier")
        if slot_index >= self.slots_per_shard:
            raise StagedPilotV3Error("runner exceeded the frozen logical-slot budget")
        expected_round, expected_candidate = divmod(
            slot_index, self.candidates_per_round
        )
        if (round_index, candidate_index) != (expected_round, expected_candidate):
            raise StagedPilotV3Error("runner logical-slot ordering drifted")
        if max_output_tokens != self.response_contract.max_output_tokens:
            raise StagedPilotV3Error("runner output-token cap drifted from route contract")
        self._assert_runtime_route_unchanged()
        prepared = self.generator.prepare_request(
            prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            round_index=round_index,
            candidate_index=candidate_index,
            seed=seed,
            state=state,
        )
        prompt_sha256 = sha256_bytes(prompt.encode("utf-8"))
        self._verify_prepared_binding(
            slot_index=slot_index,
            prepared=prepared,
            prompt_sha256=prompt_sha256,
        )
        recovery = inspect_slot_state(
            self.campaign_dir, self.shard_index, slot_index
        )
        if recovery.status != expected_slot_status:
            raise PilotCheckpointV3Error("slot state changed during locked preflight")
        if recovery.status == "committed":
            checkpoint = load_call_checkpoint(
                self.campaign_dir, self.shard_index, slot_index
            )["payload"]
            for field, expected in (
                ("request_body_sha256", prepared.body_sha256),
                ("prompt_sha256", prompt_sha256),
                ("route_binding_sha256", self.route_binding_sha256),
                ("transaction_binding_sha256", self.transaction_binding_sha256),
            ):
                if checkpoint[field] != expected:
                    raise PilotCheckpointV3Error(
                        f"committed call checkpoint {field} drifted"
                    )
            self.response_contract.validate_checkpoint_payload(
                checkpoint["response"]
            )
            response = _generation_response(checkpoint["response"])
            self.call_checkpoint_replays += 1
            self.logical_calls_seen += 1
            self._accepted.append((checkpoint["accepted_attempt"], response))
            return response
        if recovery.status == "unresolved":
            raise V3SlotUnresolvedError(
                "physical attempt start has no durable outcome; campaign is "
                "engineering-indeterminate"
            )
        if recovery.status == "exhausted":
            raise V3TransportExhaustedError(
                "logical slot exhausted all frozen physical attempts"
            )
        if recovery.status == "fatal":
            raise V3SlotFatalError("logical slot already has a fatal durable outcome")

        attempt = recovery.next_attempt
        while attempt is not None and attempt <= MAX_PHYSICAL_ATTEMPTS:
            self._assert_runtime_route_unchanged()
            start = publish_attempt_start(
                self.campaign_dir,
                shard_index=self.shard_index,
                slot_index=slot_index,
                attempt_ordinal=attempt,
                request_body_sha256=prepared.body_sha256,
                prompt_sha256=prompt_sha256,
                route_binding_sha256=self.route_binding_sha256,
                transaction_binding_sha256=self.transaction_binding_sha256,
                forbidden_values=self.forbidden_values,
            )
            try:
                self._assert_runtime_route_unchanged()
                response = self.generator.send_prepared(prepared)
                self.response_contract.validate(response)
            except TransportError as exc:
                outcome_class = (
                    "retryable_transport"
                    if exc.retryable_physical_attempt
                    else "fatal_transport"
                )
                publish_attempt_outcome(
                    self.campaign_dir,
                    shard_index=self.shard_index,
                    slot_index=slot_index,
                    attempt_ordinal=attempt,
                    outcome_class=outcome_class,
                    failure_category=exc.category,
                    forbidden_values=self.forbidden_values,
                )
                if not exc.retryable_physical_attempt:
                    raise
            except HTTPStatusError as exc:
                outcome_class = (
                    "retryable_http"
                    if exc.retryable_physical_attempt
                    else "fatal_http"
                )
                publish_attempt_outcome(
                    self.campaign_dir,
                    shard_index=self.shard_index,
                    slot_index=slot_index,
                    attempt_ordinal=attempt,
                    outcome_class=outcome_class,
                    http_status=exc.status_code,
                    forbidden_values=self.forbidden_values,
                )
                if not exc.retryable_physical_attempt:
                    raise
            except UsagePayloadError:
                publish_attempt_outcome(
                    self.campaign_dir,
                    shard_index=self.shard_index,
                    slot_index=slot_index,
                    attempt_ordinal=attempt,
                    outcome_class="fatal_response_contract",
                    failure_category="usage_payload",
                    http_status=None,
                    forbidden_values=self.forbidden_values,
                )
                raise
            except ResponsePayloadError:
                publish_attempt_outcome(
                    self.campaign_dir,
                    shard_index=self.shard_index,
                    slot_index=slot_index,
                    attempt_ordinal=attempt,
                    outcome_class="fatal_response_contract",
                    failure_category="response_payload",
                    http_status=None,
                    forbidden_values=self.forbidden_values,
                )
                raise
            except V3ResponseContractError as exc:
                publish_attempt_outcome(
                    self.campaign_dir,
                    shard_index=self.shard_index,
                    slot_index=slot_index,
                    attempt_ordinal=attempt,
                    outcome_class="fatal_response_contract",
                    failure_category=exc.category,
                    http_status=None,
                    known_input_tokens=response.input_tokens,
                    known_output_tokens=response.output_tokens,
                    known_latency_ms=float(response.latency_ms),
                    forbidden_values=self.forbidden_values,
                )
                raise
            else:
                checkpoint_payload = {
                    "shard_index": self.shard_index,
                    "slot_index": slot_index,
                    "accepted_attempt": attempt,
                    "request_body_sha256": prepared.body_sha256,
                    "prompt_sha256": prompt_sha256,
                    "route_binding_sha256": self.route_binding_sha256,
                    "transaction_binding_sha256": self.transaction_binding_sha256,
                    "accepted_start_payload_sha256": start["payload_sha256"],
                    "coordinator_version": V3_COORDINATOR_VERSION,
                    "response": _checkpoint_response(response),
                }
                published = publish_call_checkpoint(
                    self.campaign_dir,
                    checkpoint_payload,
                    forbidden_values=self.forbidden_values,
                )
                durable = _generation_response(published["payload"]["response"])
                self.logical_calls_seen += 1
                self._accepted.append((attempt, durable))
                return durable

            recovery = inspect_slot_state(
                self.campaign_dir, self.shard_index, slot_index
            )
            if recovery.status == "exhausted":
                raise V3TransportExhaustedError(
                    "logical slot exhausted all frozen physical attempts"
                )
            if recovery.status != "ready_for_retry":
                raise StagedPilotV3Error(
                    f"retryable outcome produced invalid state {recovery.status}"
                )
            attempt = recovery.next_attempt
        raise V3TransportExhaustedError(
            "logical slot exhausted all frozen physical attempts"
        )

    def execution_audit(self) -> dict[str, Any]:
        """Return a binding-aware disk audit under the episode shard lock."""

        if self._lock_context is not None:
            return self._execution_audit_locked()
        with acquire_shard_lock(
            self.campaign_dir,
            self.shard_index,
            blocking=False,
        ):
            return self._execution_audit_locked()

    def _execution_audit_locked(self) -> dict[str, Any]:
        """Return accepted telemetry separately from gross operational usage."""

        validate_artifact_inventory(self.campaign_dir)
        recovery = inspect_shard_prefix(self.campaign_dir, self.shard_index)
        states = list(recovery.slot_states)
        for slot_index, state in enumerate(states):
            for ordinal in state.started_attempts:
                start = load_attempt_start(
                    self.campaign_dir,
                    self.shard_index,
                    slot_index,
                    ordinal,
                )["payload"]
                if start["route_binding_sha256"] != self.route_binding_sha256:
                    raise PilotCheckpointV3Error(
                        "audit found a foreign provider route binding"
                    )
                if (
                    start["transaction_binding_sha256"]
                    != self.transaction_binding_sha256
                ):
                    raise PilotCheckpointV3Error(
                        "audit found a foreign campaign transaction binding"
                    )
        physical_starts = sum(len(state.started_attempts) for state in states)
        retry_count = sum(
            max(0, len(state.started_attempts) - 1) for state in states
        )
        slots_with_retry = sum(len(state.started_attempts) > 1 for state in states)
        outcome_counts: dict[str, int] = {}
        failure_category_counts: dict[str, int] = {}
        http_status_counts: dict[str, int] = {}
        discarded_known_input = 0
        discarded_known_output = 0
        discarded_known_latency = 0.0
        discarded_known_response_count = 0
        for slot_index, state in enumerate(states):
            for ordinal in state.started_attempts:
                path = attempt_outcome_path(
                    self.campaign_dir, self.shard_index, slot_index, ordinal
                )
                if not path.exists():
                    continue
                outcome = load_attempt_outcome(
                    self.campaign_dir,
                    self.shard_index,
                    slot_index,
                    ordinal,
                )["payload"]
                outcome_class = outcome["outcome_class"]
                outcome_counts[outcome_class] = (
                    outcome_counts.get(outcome_class, 0) + 1
                )
                category = outcome["failure_category"]
                if category is not None:
                    failure_category_counts[category] = (
                        failure_category_counts.get(category, 0) + 1
                    )
                status = outcome["http_status"]
                if status is not None:
                    key = str(status)
                    http_status_counts[key] = http_status_counts.get(key, 0) + 1
                if outcome["known_input_tokens"] is not None:
                    discarded_known_response_count += 1
                    discarded_known_input += int(outcome["known_input_tokens"])
                    discarded_known_output += int(outcome["known_output_tokens"])
                    discarded_known_latency += float(outcome["known_latency_ms"])
        checkpoints = [
            load_call_checkpoint(self.campaign_dir, self.shard_index, slot_index)[
                "payload"
            ]
            for slot_index, state in enumerate(states)
            if state.status == "committed"
        ]
        for checkpoint in checkpoints:
            if checkpoint["route_binding_sha256"] != self.route_binding_sha256:
                raise PilotCheckpointV3Error(
                    "audit found a foreign call route binding"
                )
            if (
                checkpoint["transaction_binding_sha256"]
                != self.transaction_binding_sha256
            ):
                raise PilotCheckpointV3Error(
                    "audit found a foreign call transaction binding"
                )
            self.response_contract.validate_checkpoint_payload(
                checkpoint["response"]
            )
        accepted_input = sum(
            item["response"]["input_tokens"] for item in checkpoints
        )
        accepted_output = sum(
            item["response"]["output_tokens"] for item in checkpoints
        )
        accepted_latency = sum(
            float(item["response"]["latency_ms"]) for item in checkpoints
        )
        unsafe_statuses = {"ready_for_retry", "unresolved", "exhausted", "fatal"}
        unsafe_slots = sum(state.status in unsafe_statuses for state in states)
        retryable_outcomes = sum(
            count
            for name, count in outcome_counts.items()
            if name.startswith("retryable_")
        )
        shard_complete = len(checkpoints) == self.slots_per_shard
        recovery_clean = (
            shard_complete and retryable_outcomes == 0 and unsafe_slots == 0
        )
        return {
            "estimand": V3_ACCEPTED_ATTEMPT_ESTIMAND,
            "logical_calls_seen": self.logical_calls_seen,
            "durable_logical_call_checkpoints": len(checkpoints),
            "shard_complete": shard_complete,
            "physical_request_starts": physical_starts,
            "physical_request_start_markers": physical_starts,
            "start_markers_are_not_confirmed_provider_receipts": True,
            "slots_with_retry": slots_with_retry,
            "retry_count": retry_count,
            "outcome_class_counts": dict(sorted(outcome_counts.items())),
            "failure_category_counts": dict(sorted(failure_category_counts.items())),
            "http_status_counts": dict(sorted(http_status_counts.items())),
            "unresolved_slot_count": sum(
                state.status == "unresolved" for state in states
            ),
            "exhausted_slot_count": sum(
                state.status == "exhausted" for state in states
            ),
            "fatal_slot_count": sum(state.status == "fatal" for state in states),
            "ready_for_retry_slot_count": sum(
                state.status == "ready_for_retry" for state in states
            ),
            "accepted_attempt_ordinals": [
                item["accepted_attempt"] for item in checkpoints
            ],
            "call_checkpoint_replays": self.call_checkpoint_replays,
            "content_retry_count": 0,
            "accepted_known_input_tokens": accepted_input,
            "accepted_known_output_tokens": accepted_output,
            "accepted_known_latency_ms": accepted_latency,
            "discarded_known_response_count": discarded_known_response_count,
            "discarded_known_input_tokens": discarded_known_input,
            "discarded_known_output_tokens": discarded_known_output,
            "discarded_known_latency_ms": discarded_known_latency,
            "known_usage_response_count": len(checkpoints)
            + discarded_known_response_count,
            "usage_unknown_start_marker_count": physical_starts
            - len(checkpoints)
            - discarded_known_response_count,
            "gross_known_token_lower_bound": (
                accepted_input
                + accepted_output
                + discarded_known_input
                + discarded_known_output
            ),
            "gross_known_latency_ms": accepted_latency + discarded_known_latency,
            "gross_usage_complete": recovery_clean,
            # The campaign-level 2% realized-token gate is still required;
            # this shard-level flag speaks only to recovery contamination.
            "recovery_allows_actual_token_matched_claim": recovery_clean,
        }


__all__ = [
    "AcceptedResponseContract",
    "DurableLogicalSlotGenerator",
    "FrozenTransactionIdentity",
    "StagedPilotV3Error",
    "V3_ACCEPTED_ATTEMPT_ESTIMAND",
    "V3_CANDIDATES_PER_ROUND",
    "V3ResponseContractError",
    "V3SlotFatalError",
    "V3SlotUnresolvedError",
    "V3TransportExhaustedError",
    "route_binding_sha256",
]
