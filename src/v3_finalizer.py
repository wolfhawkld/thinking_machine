"""Offline, two-pass private-test finalization for V3 development campaigns.

The generation campaign deliberately cannot see a world's hidden test split.
This module is the only bridge across that boundary.  Its first pass validates
the complete main-grid commit set and deterministically replays all 20 accepted
responses per episode with ``evaluate_test=False``.  Only after the immutable
generation barrier has also been verified does a second pass evaluate selected
candidates on the private test.

No provider adapter or credential is accepted by this module, and replay never
performs network I/O.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from fractions import Fraction
import inspect
import json
import math
from pathlib import Path
import random
from typing import Any

from .pilot_checkpoint import (
    INVALID_CANDIDATE_SENTINEL,
    atomic_publish_public_snapshot,
    canonical_json_bytes,
    sha256_bytes,
    sha256_json,
)
from .pilot_checkpoint_v3 import (
    SLOTS_PER_SHARD,
    call_checkpoint_path,
    inspect_shard_prefix,
    load_call_checkpoint,
)
from .runner import CANDIDATE_FORMATS, EpisodeResult, GenerationResponse, run_episode
from .staged_pilot_v3 import FrozenTransactionIdentity
from .v3_development import transaction_identity_payload
from .v3_generation import (
    accepted_response_contract,
    episode_metrics,
    generation_state_sha256,
    generation_world_view,
    policy_for_entry,
)
from .verifier import Verifier
from .world_generator import generate_world


V3_EXPECTED_MAIN_EPISODES = 96
V3_TEST_POINTS_PER_EPISODE = 64
V3_FINALIZER_MODE = "offline-two-pass-private-finalizer"
V3_BOOTSTRAP_REPLICATES = 100_000
V3_BOOTSTRAP_SEED = 20_260_809
V3_SIGN_FLIP_PATTERNS = 4_096
V3_FINAL_SNAPSHOT_NAME = "v3-finalized-snapshot.json"


class V3FinalizationError(RuntimeError):
    """The V3 private endpoint cannot be released safely."""


def _payload(envelope: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if not isinstance(envelope, Mapping):
        raise V3FinalizationError(f"{name} is not an object")
    value = envelope.get("payload", envelope)
    if not isinstance(value, Mapping):
        raise V3FinalizationError(f"{name} payload is not an object")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _manifest_payload_sha256(
    manifest_envelope: Mapping[str, Any], manifest: Mapping[str, Any]
) -> str:
    value = manifest_envelope.get("payload_sha256")
    computed = sha256_json(manifest)
    if value is not None and value != computed:
        raise V3FinalizationError("campaign manifest envelope hash drifted")
    return computed


def _main_entries(
    manifest: Mapping[str, Any], *, expected_main_entries: int
) -> list[dict[str, Any]]:
    if type(expected_main_entries) is not int or expected_main_entries < 1:
        raise ValueError("expected_main_entries must be a positive integer")
    plan = manifest.get("execution_plan")
    if not isinstance(plan, Sequence) or isinstance(plan, (str, bytes)):
        raise V3FinalizationError("campaign manifest lacks an execution plan")
    entries = [dict(entry) for entry in plan if entry.get("phase") == "main"]
    if len(entries) != expected_main_entries:
        raise V3FinalizationError(
            f"main-grid cardinality is {len(entries)}, expected {expected_main_entries}"
        )
    if expected_main_entries == V3_EXPECTED_MAIN_EPISODES:
        model_strata = {
            str(item.get("stratum_id"))
            for item in manifest.get("frozen_config", {}).get("model_strata", [])
        }
        if len(model_strata) != 2:
            raise V3FinalizationError("production V3 requires exactly two model strata")
        expected_cells = {
            (model, arm): 12
            for model in model_strata
            for arm in ("L", "H", "C", "E2")
        }
        observed: dict[tuple[str, str], int] = {}
        for entry in entries:
            key = (str(entry.get("model_stratum")), str(entry.get("arm_id")))
            observed[key] = observed.get(key, 0) + 1
        if observed != expected_cells:
            raise V3FinalizationError("production V3 main grid is not balanced 2x12x4")
    return entries


def _generation_response(payload: Mapping[str, Any]) -> GenerationResponse:
    """Rebuild only the normalized accepted response stored in a checkpoint."""

    try:
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
            # Only the frozen fingerprint digest is durable.  The raw provider
            # value is neither needed by selection nor allowed in final output.
            provider_fingerprint=None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V3FinalizationError("call checkpoint response cannot be replayed") from exc


class _CheckpointReplayGenerator:
    """Strictly consume an ordered, already-validated set of 20 checkpoints."""

    def __init__(self, checkpoints: Sequence[Mapping[str, Any]]) -> None:
        if len(checkpoints) != SLOTS_PER_SHARD:
            raise V3FinalizationError("episode replay requires exactly 20 calls")
        self._checkpoints = tuple(checkpoints)
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
        slot_index = self.calls
        if slot_index >= len(self._checkpoints):
            raise V3FinalizationError("runner exceeded the committed call sequence")
        expected_round, expected_candidate = divmod(slot_index, 4)
        if (round_index, candidate_index) != (expected_round, expected_candidate):
            raise V3FinalizationError("runner logical-slot order drifted during replay")
        if max_output_tokens != 256:
            raise V3FinalizationError("runner output-token cap drifted during replay")
        checkpoint = self._checkpoints[slot_index]
        if checkpoint.get("slot_index") != slot_index:
            raise V3FinalizationError("call checkpoint slot ordering drifted")
        prompt_digest = sha256_bytes(prompt.encode("utf-8"))
        if checkpoint.get("prompt_sha256") != prompt_digest:
            raise V3FinalizationError(
                "offline replay prompt disagrees with the committed checkpoint"
            )
        response = checkpoint.get("response")
        if not isinstance(response, Mapping):
            raise V3FinalizationError("call checkpoint response is missing")
        self.calls += 1
        return _generation_response(response)


def _ordered_calls(
    root: Path,
    *,
    manifest: Mapping[str, Any],
    manifest_payload_sha256: str,
    entry: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Load and bind a complete shard without consulting private-test data."""

    shard_index = entry.get("shard_index")
    if type(shard_index) is not int:
        raise V3FinalizationError("main plan entry lacks an integer shard index")
    recovery = inspect_shard_prefix(root, shard_index)
    if recovery.committed_prefix_count != SLOTS_PER_SHARD:
        raise V3FinalizationError(
            f"main shard {shard_index} lacks its complete 20-call commit prefix"
        )
    try:
        identity = FrozenTransactionIdentity(
            **transaction_identity_payload(manifest, entry)
        )
    except Exception as exc:
        raise V3FinalizationError("main shard transaction identity drifted") from exc
    if identity.campaign_manifest_payload_sha256 != manifest_payload_sha256:
        raise V3FinalizationError("transaction does not bind the loaded manifest")
    contract = accepted_response_contract(manifest, str(entry["model_stratum"]))
    calls: list[dict[str, Any]] = []
    payload_hashes: list[str] = []
    file_hashes: list[str] = []
    for slot_index in range(SLOTS_PER_SHARD):
        envelope = load_call_checkpoint(root, shard_index, slot_index)
        payload = _payload(envelope, "call checkpoint")
        exact = {
            "shard_index": shard_index,
            "slot_index": slot_index,
            "route_binding_sha256": entry.get("route_binding_sha256"),
            "transaction_binding_sha256": identity.binding_sha256,
        }
        if any(payload.get(key) != value for key, value in exact.items()):
            raise V3FinalizationError(
                f"call checkpoint {shard_index}/{slot_index} binding drifted"
            )
        response = payload.get("response")
        if not isinstance(response, Mapping):
            raise V3FinalizationError("call checkpoint response is not an object")
        try:
            contract.validate_checkpoint_payload(response)
        except Exception as exc:
            raise V3FinalizationError(
                "accepted response no longer satisfies the frozen route contract"
            ) from exc
        payload_hash = envelope.get("payload_sha256")
        if not _is_sha256(payload_hash) or payload_hash != sha256_json(payload):
            raise V3FinalizationError("call checkpoint payload digest drifted")
        path = call_checkpoint_path(root, shard_index, slot_index)
        try:
            file_hash = sha256_bytes(path.read_bytes())
        except OSError as exc:
            raise V3FinalizationError("cannot hash a committed call checkpoint") from exc
        calls.append(dict(payload))
        payload_hashes.append(payload_hash)
        file_hashes.append(file_hash)
    return calls, payload_hashes, file_hashes


def _seal_sequence(value: Any, *names: str) -> list[Any] | None:
    if not isinstance(value, Mapping):
        return None
    for name in names:
        candidate = value.get(name)
        if candidate is not None:
            if not isinstance(candidate, Sequence) or isinstance(candidate, (str, bytes)):
                raise V3FinalizationError(f"episode seal {name} is not an array")
            return list(candidate)
    return None


def _verify_seal_replay(
    seal_payload: Mapping[str, Any],
    *,
    entry: Mapping[str, Any],
    metrics: Mapping[str, Any],
    call_payload_hashes: Sequence[str],
    call_file_hashes: Sequence[str],
) -> None:
    """Compare replay state with the seal while tolerating no missing binding."""

    if seal_payload.get("private_test_evaluated") is not False:
        raise V3FinalizationError("episode seal crossed the private-test boundary")
    if seal_payload.get("generation_state_sha256") != metrics.get(
        "generation_state_sha256"
    ):
        raise V3FinalizationError("episode generation-state digest disagrees with replay")
    stored_metrics = seal_payload.get(
        "episode_metrics",
        seal_payload.get("metrics", seal_payload.get("safe_metrics")),
    )
    if stored_metrics != metrics:
        raise V3FinalizationError("episode safe metrics disagree with deterministic replay")
    stored_payload_hashes = _seal_sequence(
        seal_payload,
        "ordered_call_checkpoint_payload_sha256",
        "ordered_call_payload_sha256",
    )
    if stored_payload_hashes != list(call_payload_hashes):
        raise V3FinalizationError("episode seal call-payload order/hash drifted")
    call_references = seal_payload.get("ordered_call_checkpoints")
    if call_references is not None:
        if not isinstance(call_references, Sequence) or isinstance(
            call_references, (str, bytes)
        ) or len(call_references) != SLOTS_PER_SHARD:
            raise V3FinalizationError("episode seal checkpoint-file references drifted")
        for slot_index, reference in enumerate(call_references):
            if not isinstance(reference, Mapping) or (
                reference.get("slot_index") != slot_index
                or reference.get("checkpoint_payload_sha256")
                != call_payload_hashes[slot_index]
                or reference.get("checkpoint_file_sha256")
                != call_file_hashes[slot_index]
            ):
                raise V3FinalizationError(
                    "episode seal checkpoint-file order/hash drifted"
                )
    stored_file_hashes = _seal_sequence(
        seal_payload,
        "ordered_call_checkpoint_file_sha256",
        "ordered_call_file_sha256",
    )
    if stored_file_hashes is not None and stored_file_hashes != list(call_file_hashes):
        raise V3FinalizationError("episode seal call-file order/hash drifted")
    for field in (
        "shard_index",
        "plan_entry_sha256",
        "run_id",
        "route_binding_sha256",
    ):
        if field in seal_payload and seal_payload[field] != entry.get(field):
            raise V3FinalizationError(f"episode seal {field} drifted from plan")


def _replay_episode(
    root: Path,
    *,
    manifest_envelope: Mapping[str, Any],
    manifest: Mapping[str, Any],
    manifest_payload_sha256: str,
    entry: Mapping[str, Any],
    load_episode_seal: Callable[..., Mapping[str, Any]],
) -> tuple[EpisodeResult, Mapping[str, Any], Mapping[str, Any]]:
    calls, payload_hashes, file_hashes = _ordered_calls(
        root,
        manifest=manifest,
        manifest_payload_sha256=manifest_payload_sha256,
        entry=entry,
    )
    replay = _CheckpointReplayGenerator(calls)
    # Pass 1 gets the same reduced surface as live generation.  In particular,
    # private-test examples and the hidden law are not retained or exposed to
    # replay before the 104-seal generation barrier has been verified.
    world = generation_world_view(entry)
    frozen = manifest["frozen_config"]
    episode = frozen["episode"]
    verifier = Verifier(
        counterexample_limit=int(episode["max_counterexamples_per_round"])
    )
    result = run_episode(
        world,
        replay,
        verifier=verifier,
        policy=policy_for_entry(manifest, entry),
        rounds=int(episode["rounds"]),
        candidates_per_round=int(episode["candidates_per_round"]),
        archive_capacity=int(episode["archive_size"]),
        max_counterexamples=(
            int(episode["rounds"])
            * int(episode["max_counterexamples_per_round"])
        ),
        seed=int(entry["sampling_base_seed"]),
        max_output_tokens=int(episode["max_output_tokens"]),
        max_counterexamples_per_round=int(
            episode["max_counterexamples_per_round"]
        ),
        evaluate_test=False,
    )
    if replay.calls != SLOTS_PER_SHARD or result.candidate_count != SLOTS_PER_SHARD:
        raise V3FinalizationError("offline replay did not consume exactly 20 calls")
    metrics = episode_metrics(result, entry)
    if metrics.get("generation_state_sha256") != generation_state_sha256(result):
        raise V3FinalizationError("generation digest helper disagreement")
    seal_envelope = load_episode_seal(
        root, manifest_envelope, int(entry["shard_index"])
    )
    seal_payload = _payload(seal_envelope, "episode seal")
    _verify_seal_replay(
        seal_payload,
        entry=entry,
        metrics=metrics,
        call_payload_hashes=payload_hashes,
        call_file_hashes=file_hashes,
    )
    return result, metrics, seal_payload


def _call_injected_evaluator(
    evaluator: Callable[..., Any],
    *,
    result: EpisodeResult,
    verifier: Verifier,
    world: Any,
    entry: Mapping[str, Any],
) -> Any:
    """Support small deterministic test hooks without weakening production."""

    try:
        signature = inspect.signature(evaluator)
    except (TypeError, ValueError):
        return evaluator(result, verifier, world, entry)
    available = {
        "result": result,
        "episode_result": result,
        "verifier": verifier,
        "world": world,
        "entry": entry,
        "plan_entry": entry,
        "candidate": result.final_candidate.candidate,
    }
    positional: list[Any] = []
    keyword: dict[str, Any] = {}
    fallback = iter((result, verifier, world, entry))
    for parameter in signature.parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            positional.extend(fallback)
            continue
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        value = available.get(parameter.name)
        if value is None and parameter.default is inspect.Parameter.empty:
            try:
                value = next(fallback)
            except StopIteration as exc:
                raise TypeError("private test evaluator signature is unsupported") from exc
        elif value is None:
            continue
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY:
            keyword[parameter.name] = value
        else:
            positional.append(value)
    return evaluator(*positional, **keyword)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _private_endpoint(
    result: EpisodeResult,
    metrics: Mapping[str, Any],
    *,
    verifier: Verifier,
    world: Any,
    entry: Mapping[str, Any],
    test_evaluator: Callable[..., Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Return one prospectively coded endpoint and whether a test was invoked."""

    all_invalid = metrics.get("all_invalid") is True
    selected = result.final_candidate
    if all_invalid:
        if selected is not None or metrics.get("search_valid_count") != 0:
            raise V3FinalizationError("all-invalid endpoint state is inconsistent")
        return (
            {
                "outcome_status": "all_invalid",
                "observed_accuracy": None,
                "primary_correct": 0,
                "primary_denominator": V3_TEST_POINTS_PER_EPISODE,
                "primary_score": 0.0,
                "world_solved": False,
                "zero_is_observed_accuracy": False,
            },
            False,
        )
    if selected is None:
        raise V3FinalizationError(
            "search-valid candidates exist but final selection is absent"
        )
    try:
        if test_evaluator is None:
            evaluated = verifier.verify_test(selected.candidate, tuple(world.test))
        else:
            evaluated = _call_injected_evaluator(
                test_evaluator,
                result=result,
                verifier=verifier,
                world=world,
                entry=entry,
            )
    except Exception as exc:
        # A scientific candidate runtime failure is returned by Verifier as a
        # normalized ``runtime_valid=False`` result below.  An exception that
        # escapes the evaluator is instead an evaluator/code/infrastructure
        # failure and must not be silently converted into a scientific zero.
        raise V3FinalizationError("private-test evaluator failed unexpectedly") from exc
    runtime_valid = _field(evaluated, "runtime_valid", True)
    if runtime_valid is not True:
        return (
            {
                "outcome_status": "test_runtime_failure",
                "observed_accuracy": None,
                "primary_correct": 0,
                "primary_denominator": V3_TEST_POINTS_PER_EPISODE,
                "primary_score": 0.0,
                "world_solved": False,
                "zero_is_observed_accuracy": False,
            },
            True,
        )
    source = _field(evaluated, "raw", None) or evaluated
    correct = _field(source, "correct", None)
    total = _field(source, "total", None)
    score = _field(evaluated, "score", _field(evaluated, "probe_accuracy", None))
    if score is None:
        score = _field(source, "score", _field(source, "probe_accuracy", None))
    if total is None:
        total = V3_TEST_POINTS_PER_EPISODE
    if type(total) is not int or total != V3_TEST_POINTS_PER_EPISODE:
        raise V3FinalizationError("normal private-test result must contain 64 points")
    if correct is None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise V3FinalizationError("normal private-test result lacks integer correct")
        scaled = float(score) * V3_TEST_POINTS_PER_EPISODE
        if not math.isfinite(scaled) or not scaled.is_integer():
            raise V3FinalizationError("private-test accuracy is not exact correct/64")
        correct = int(scaled)
    if type(correct) is not int or not 0 <= correct <= total:
        raise V3FinalizationError("private-test correct must be an integer in [0, 64]")
    exact_score = correct / V3_TEST_POINTS_PER_EPISODE
    if score is not None and (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(float(score))
        or float(score) != exact_score
    ):
        raise V3FinalizationError("private-test score disagrees with exact correct/64")
    return (
        {
            "outcome_status": "evaluated",
            "observed_accuracy": exact_score,
            "primary_correct": correct,
            "primary_denominator": V3_TEST_POINTS_PER_EPISODE,
            "primary_score": exact_score,
            "world_solved": correct == V3_TEST_POINTS_PER_EPISODE,
            "zero_is_observed_accuracy": True,
        },
        True,
    )


def _aggregates(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for run in runs:
        groups.setdefault(
            (str(run["model_stratum"]), str(run["arm_id"])), []
        ).append(run)
    result: list[dict[str, Any]] = []
    for (model, arm), items in sorted(groups.items()):
        correct = sum(int(item["primary_correct"]) for item in items)
        denominator = len(items) * V3_TEST_POINTS_PER_EPISODE
        observed = [
            float(item["observed_accuracy"])
            for item in items
            if item["observed_accuracy"] is not None
        ]
        result.append(
            {
                "model_stratum": model,
                "arm_id": arm,
                "episode_count": len(items),
                "primary_correct": correct,
                "primary_denominator": denominator,
                "mean_primary_score": correct / denominator,
                "mean_observed_accuracy_success_only": (
                    None if not observed else sum(observed) / len(observed)
                ),
                "all_invalid_episode_count": sum(
                    item["outcome_status"] == "all_invalid" for item in items
                ),
                "test_runtime_failure_count": sum(
                    item["outcome_status"] == "test_runtime_failure"
                    for item in items
                ),
            }
        )
    return result


def _nearest_rank_interval(values: list[float]) -> dict[str, float]:
    if not values:
        raise V3FinalizationError("bootstrap distribution is empty")
    ordered = sorted(values)

    def percentile(probability: float) -> float:
        index = math.ceil(probability * len(ordered)) - 1
        return ordered[max(0, min(len(ordered) - 1, index))]

    return {
        "lower": percentile(0.025),
        "upper": percentile(0.975),
    }


def _sign_flip_p_value(differences: Sequence[int]) -> float:
    if len(differences) != 12:
        raise V3FinalizationError("sign-flip analysis requires exactly 12 worlds")
    observed = abs(sum(differences))
    extreme = 0
    for pattern in range(V3_SIGN_FLIP_PATTERNS):
        permuted = sum(
            value if pattern & (1 << index) else -value
            for index, value in enumerate(differences)
        )
        if abs(permuted) >= observed:
            extreme += 1
    return extreme / V3_SIGN_FLIP_PATTERNS


def _paired_statistical_analysis(
    runs: Sequence[Mapping[str, Any]],
    model_order: Sequence[str],
    *,
    bootstrap_replicates: int = V3_BOOTSTRAP_REPLICATES,
    bootstrap_seed: int = V3_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Apply the frozen paired-world SAP to a complete 2x12x4 grid."""

    if list(dict.fromkeys(model_order)) != list(model_order) or len(model_order) != 2:
        raise V3FinalizationError("paired analysis requires two ordered model strata")
    if type(bootstrap_replicates) is not int or bootstrap_replicates < 1:
        raise ValueError("bootstrap_replicates must be a positive integer")
    cells: dict[tuple[str, int, str], Mapping[str, Any]] = {}
    world_metadata: dict[int, tuple[int, int]] = {}
    for run in runs:
        model = str(run["model_stratum"])
        world = int(run["world_index"])
        arm = str(run["arm_id"])
        key = (model, world, arm)
        if key in cells:
            raise V3FinalizationError("paired analysis found a duplicate grid cell")
        cells[key] = run
        metadata = (int(run["world_seed"]), int(run["depth"]))
        if world in world_metadata and world_metadata[world] != metadata:
            raise V3FinalizationError("shared world metadata drifted across grid cells")
        world_metadata[world] = metadata
    worlds = sorted(world_metadata)
    if len(worlds) != 12:
        raise V3FinalizationError("paired analysis requires exactly 12 shared worlds")
    expected_cells = {
        (model, world, arm)
        for model in model_order
        for world in worlds
        for arm in ("L", "H", "C", "E2")
    }
    if set(cells) != expected_cells:
        raise V3FinalizationError("paired analysis requires the complete 2x12x4 grid")

    per_model: dict[str, dict[int, int]] = {}
    route_rows: dict[str, list[dict[str, Any]]] = {}
    for model in model_order:
        differences: dict[int, int] = {}
        rows: list[dict[str, Any]] = []
        for world in worlds:
            try:
                adaptive = cells[(model, world, "E2")]
                reference = cells[(model, world, "C")]
            except KeyError as exc:
                raise V3FinalizationError("paired E2/C grid cell is missing") from exc
            difference = int(adaptive["primary_correct"]) - int(
                reference["primary_correct"]
            )
            differences[world] = difference
            seed, depth = world_metadata[world]
            rows.append(
                {
                    "world_index": world,
                    "world_seed": seed,
                    "depth": depth,
                    "E2_primary_correct": int(adaptive["primary_correct"]),
                    "C_primary_correct": int(reference["primary_correct"]),
                    "difference_correct": difference,
                    "difference_accuracy": difference / 64.0,
                }
            )
        per_model[model] = differences
        route_rows[model] = rows

    depth_groups: dict[int, list[int]] = {}
    for world, (_seed, depth) in world_metadata.items():
        depth_groups.setdefault(depth, []).append(world)
    if sorted((depth, len(items)) for depth, items in depth_groups.items()) != [
        (3, 4),
        (4, 4),
        (5, 4),
    ]:
        raise V3FinalizationError("depth-stratified SAP requires 4 worlds at each depth")
    for items in depth_groups.values():
        items.sort()

    route_totals = {
        model: sum(per_model[model][world] for world in worlds)
        for model in model_order
    }
    route_denominator = 12 * 64
    combined_total = sum(route_totals.values())
    combined_denominator = 2 * route_denominator

    rng = random.Random(bootstrap_seed)
    distributions = {model: [] for model in model_order}
    equal_distribution: list[float] = []
    ordered_depths = sorted(depth_groups)
    for _ in range(bootstrap_replicates):
        selected: list[int] = []
        for depth in ordered_depths:
            group = depth_groups[depth]
            selected.extend(group[rng.randrange(4)] for _slot in range(4))
        totals = {
            model: sum(per_model[model][world] for world in selected)
            for model in model_order
        }
        for model in model_order:
            distributions[model].append(totals[model] / route_denominator)
        equal_distribution.append(sum(totals.values()) / combined_denominator)

    per_depth: list[dict[str, Any]] = []
    for depth in ordered_depths:
        group = depth_groups[depth]
        model_deltas = {
            model: sum(per_model[model][world] for world in group) / (4 * 64)
            for model in model_order
        }
        per_depth.append(
            {
                "depth": depth,
                "world_count": 4,
                "model_deltas": model_deltas,
                "equal_stratum_delta": sum(model_deltas.values()) / 2.0,
                "scope": "descriptive_only",
            }
        )

    equal_world_differences = [
        sum(per_model[model][world] for model in model_order) for world in worlds
    ]
    return {
        "primary_unit": "world",
        "joint_cluster_count": 12,
        "shared_worlds_across_routes": True,
        "route_paired_contrasts": {
            model: {
                "worlds": route_rows[model],
                "difference_correct_total": route_totals[model],
                "difference_denominator": route_denominator,
                "mean_delta": route_totals[model] / route_denominator,
            }
            for model in model_order
        },
        "equal_stratum": {
            "difference_correct_total": combined_total,
            "difference_denominator": combined_denominator,
            "mean_delta": combined_total / combined_denominator,
        },
        "bootstrap": {
            "kind": "depth_stratified_world_cluster_percentile",
            "percentile_method": "nearest_rank_order_statistic",
            "replicates": bootstrap_replicates,
            "rng_seed": bootstrap_seed,
            "confidence_level": 0.95,
            "route_intervals": {
                model: _nearest_rank_interval(distributions[model])
                for model in model_order
            },
            "equal_stratum_interval": _nearest_rank_interval(equal_distribution),
        },
        "sign_flip": {
            "kind": "exact_two_sided_paired_world",
            "patterns": V3_SIGN_FLIP_PATTERNS,
            "route_p_values": {
                model: _sign_flip_p_value(
                    [per_model[model][world] for world in worlds]
                )
                for model in model_order
            },
            "equal_route_per_world_p_value": _sign_flip_p_value(
                equal_world_differences
            ),
            "exploratory": True,
            "multiple_testing_adjustment": "none",
        },
        "per_depth_estimates": per_depth,
    }


def _classification(
    aggregates: Sequence[Mapping[str, Any]], *, test_only: bool
) -> dict[str, Any]:
    if test_only:
        return {
            "eligible": False,
            "decision": None,
            "reason": "test-only cardinality or injected evaluator",
            "model_deltas": {},
            "equal_stratum_delta": None,
        }
    by_key = {
        (str(item["model_stratum"]), str(item["arm_id"])): item
        for item in aggregates
    }
    models = sorted({model for model, _ in by_key})
    if len(models) != 2 or any(
        (model, arm) not in by_key for model in models for arm in ("C", "E2")
    ):
        raise V3FinalizationError("complete-grid E2/C classification cells are missing")
    if any(
        int(by_key[(model, arm)]["episode_count"]) != 12
        or int(by_key[(model, arm)]["primary_denominator"]) != 12 * 64
        for model in models
        for arm in ("C", "E2")
    ):
        raise V3FinalizationError("classification requires 12 E2/C worlds per route")
    delta_correct = {
        model: int(by_key[(model, "E2")]["primary_correct"])
        - int(by_key[(model, "C")]["primary_correct"])
        for model in models
    }
    route_denominator = 12 * 64
    deltas = {
        model: delta_correct[model] / route_denominator for model in models
    }
    combined_correct = sum(delta_correct.values())
    combined_denominator = 2 * route_denominator
    exact_mean = Fraction(combined_correct, combined_denominator)
    mean_delta = float(exact_mean)
    if all(value > 0 for value in delta_correct.values()) and exact_mean >= Fraction(1, 20):
        label = "two_route_development_promising"
    elif all(value <= 0 for value in delta_correct.values()):
        label = "two_route_nonpositive_development_signal"
    else:
        label = "mixed_or_small_development_signal"
    return {
        "eligible": True,
        "decision": label,
        "model_deltas": deltas,
        "model_delta_exact": {
            model: {
                "difference_correct": delta_correct[model],
                "denominator": route_denominator,
            }
            for model in models
        },
        "equal_stratum_delta": mean_delta,
        "equal_stratum_delta_exact": {
            "difference_correct": combined_correct,
            "denominator": combined_denominator,
        },
        "minimum_important_effect": 0.05,
        "threshold_comparison": "exact_rational_no_tolerance",
        "scope": "development-only",
    }


def _construct_diagnostics(
    runs: Sequence[Mapping[str, Any]],
    model_order: Sequence[str],
    *,
    test_only: bool,
) -> dict[str, Any]:
    if test_only:
        return {
            "eligible": False,
            "reason": "test-only reduced cardinality",
        }
    routes: list[dict[str, Any]] = []
    for model in model_order:
        model_runs = [run for run in runs if run["model_stratum"] == model]
        if len(model_runs) != 48:
            raise V3FinalizationError("construct diagnostics require 48 runs per route")
        arm_rows: list[dict[str, Any]] = []
        for arm in ("L", "H", "C", "E2"):
            items = [run for run in model_runs if run["arm_id"] == arm]
            if len(items) != 12:
                raise V3FinalizationError("construct arm grid is incomplete")
            planned = 12 * 20
            valid = sum(int(item["search_valid_count"]) for item in items)
            outer = sum(int(item["outer_schema_valid_count"]) for item in items)
            canonical = sum(int(item["canonical_unique_count"]) for item in items)
            behavioral = sum(int(item["behavioral_unique_count"]) for item in items)
            arm_rows.append(
                {
                    "arm_id": arm,
                    "episode_count": 12,
                    "planned_call_count": planned,
                    "search_valid_count": valid,
                    "search_valid_rate": valid / planned,
                    "minimum_search_valid_count": 216,
                    "validity_passed": valid >= 216,
                    "outer_schema_valid_count": outer,
                    "outer_schema_valid_rate": outer / planned,
                    "canonical_unique_count": canonical,
                    "canonical_unique_yield_per_planned_call": canonical / planned,
                    "behavioral_unique_count": behavioral,
                    "behavioral_unique_yield_per_planned_call": behavioral / planned,
                    "all_invalid_episode_count": sum(
                        item["outcome_status"] == "all_invalid" for item in items
                    ),
                }
            )
        by_arm = {row["arm_id"]: row for row in arm_rows}
        overall_valid = sum(row["search_valid_count"] for row in arm_rows)
        overall_outer = sum(row["outer_schema_valid_count"] for row in arm_rows)
        overall_planned = 48 * 20
        validity_passed = overall_valid >= 912 and all(
            row["validity_passed"] for row in arm_rows
        )
        canonical_delta = (
            by_arm["H"]["canonical_unique_yield_per_planned_call"]
            - by_arm["L"]["canonical_unique_yield_per_planned_call"]
        )
        behavioral_delta = (
            by_arm["H"]["behavioral_unique_yield_per_planned_call"]
            - by_arm["L"]["behavioral_unique_yield_per_planned_call"]
        )
        manipulation_passed = canonical_delta > 0.0 and behavioral_delta > 0.0
        failure_codes: dict[str, int] = {}
        for run in model_runs:
            for code, count in run.get("failure_counts_by_code", {}).items():
                failure_codes[str(code)] = failure_codes.get(str(code), 0) + int(count)
        invalid_by_world = []
        for world in sorted({int(run["world_index"]) for run in model_runs}):
            items = [run for run in model_runs if int(run["world_index"]) == world]
            invalid = sum(20 - int(item["search_valid_count"]) for item in items)
            invalid_by_world.append(
                {
                    "world_index": world,
                    "invalid_candidate_count": invalid,
                    "planned_call_count": len(items) * 20,
                    "invalid_rate": invalid / (len(items) * 20),
                }
            )
        routes.append(
            {
                "model_stratum": model,
                "overall": {
                    "planned_call_count": overall_planned,
                    "search_valid_count": overall_valid,
                    "search_valid_rate": overall_valid / overall_planned,
                    "minimum_search_valid_count": 912,
                    "validity_passed": validity_passed,
                    "outer_schema_valid_count": overall_outer,
                    "outer_schema_valid_rate": overall_outer / overall_planned,
                },
                "arms": arm_rows,
                "manipulation": {
                    "H_minus_L_canonical_unique_yield_per_call": canonical_delta,
                    "H_minus_L_behavioral_unique_yield_per_call": behavioral_delta,
                    "passed": manipulation_passed,
                },
                "failure_counts_by_code": dict(sorted(failure_codes.items())),
                "invalid_concentration_by_world": invalid_by_world,
                "status": (
                    "passed"
                    if validity_passed and manipulation_passed
                    else (
                        "construct_validity_warning_and_manipulation_indeterminate"
                        if not validity_passed and not manipulation_passed
                        else (
                            "construct_validity_warning"
                            if not validity_passed
                            else "manipulation_indeterminate"
                        )
                    )
                ),
            }
        )
    return {
        "eligible": True,
        "routes": routes,
        "construct_validity_warning": any(
            route["overall"]["validity_passed"] is not True for route in routes
        ),
        "manipulation_indeterminate": any(
            route["manipulation"]["passed"] is not True for route in routes
        ),
        "performance_classification_still_reported": True,
    }


def _resource_sensitivity(
    runs: Sequence[Mapping[str, Any]],
    model_order: Sequence[str],
    classification: Mapping[str, Any],
    *,
    test_only: bool,
) -> dict[str, Any]:
    if test_only:
        return {
            "eligible": False,
            "reason": "test-only reduced cardinality",
        }
    model_deltas = classification["model_deltas"]
    routes: list[dict[str, Any]] = []
    for model in model_order:
        arm_rows: list[dict[str, Any]] = []
        for arm in ("L", "H", "C", "E2"):
            items = [
                run
                for run in runs
                if run["model_stratum"] == model and run["arm_id"] == arm
            ]
            if len(items) != 12 or any("resource" not in item for item in items):
                raise V3FinalizationError("resource grid is incomplete")
            planned_calls = len(items) * 20
            accepted_input = sum(
                int(item["resource"]["accepted_known_input_tokens"])
                for item in items
            )
            accepted_output = sum(
                int(item["resource"]["accepted_known_output_tokens"])
                for item in items
            )
            gross_lower = sum(
                int(item["resource"]["gross_known_token_lower_bound"])
                for item in items
            )
            arm_rows.append(
                {
                    "arm_id": arm,
                    "episode_count": 12,
                    "planned_logical_calls": planned_calls,
                    "accepted_input_tokens": accepted_input,
                    "accepted_output_tokens": accepted_output,
                    "mean_accepted_billed_tokens_per_call": (
                        accepted_input + accepted_output
                    )
                    / planned_calls,
                    "gross_known_token_lower_bound": gross_lower,
                    "mean_gross_known_token_lower_bound_per_call": gross_lower
                    / planned_calls,
                    "physical_request_start_markers": sum(
                        int(item["resource"]["physical_request_starts"])
                        for item in items
                    ),
                    "retry_count": sum(
                        int(item["resource"]["retry_count"]) for item in items
                    ),
                    "discarded_known_response_count": sum(
                        int(item["resource"]["discarded_known_response_count"])
                        for item in items
                    ),
                    "discarded_known_input_tokens": sum(
                        int(item["resource"]["discarded_known_input_tokens"])
                        for item in items
                    ),
                    "discarded_known_output_tokens": sum(
                        int(item["resource"]["discarded_known_output_tokens"])
                        for item in items
                    ),
                    "usage_unknown_start_marker_count": sum(
                        int(item["resource"]["usage_unknown_start_marker_count"])
                        for item in items
                    ),
                    "unresolved_slot_count": sum(
                        int(item["resource"]["unresolved_slot_count"])
                        for item in items
                    ),
                    "exhausted_slot_count": sum(
                        int(item["resource"]["exhausted_slot_count"])
                        for item in items
                    ),
                    "fatal_slot_count": sum(
                        int(item["resource"]["fatal_slot_count"])
                        for item in items
                    ),
                    "ready_for_retry_slot_count": sum(
                        int(item["resource"]["ready_for_retry_slot_count"])
                        for item in items
                    ),
                    "accepted_known_latency_ms": sum(
                        float(item["resource"]["accepted_known_latency_ms"])
                        for item in items
                    ),
                    "gross_known_latency_ms": sum(
                        float(item["resource"]["gross_known_latency_ms"])
                        for item in items
                    ),
                    "discarded_known_latency_ms": sum(
                        float(item["resource"]["discarded_known_latency_ms"])
                        for item in items
                    ),
                    "gross_usage_complete": all(
                        item["resource"]["gross_usage_complete"] is True
                        for item in items
                    ),
                    "recovery_allows_actual_token_matched_claim": all(
                        item["resource"][
                            "recovery_allows_actual_token_matched_claim"
                        ]
                        is True
                        for item in items
                    ),
                }
            )
        by_arm = {row["arm_id"]: row for row in arm_rows}
        token_rates = [
            Fraction(
                int(row["gross_known_token_lower_bound"]),
                int(row["planned_logical_calls"]),
            )
            for row in arm_rows
        ]
        relative_range_exact = (
            (max(token_rates) - min(token_rates)) / min(token_rates)
            if min(token_rates) > 0
            else None
        )
        relative_range = (
            float(relative_range_exact)
            if relative_range_exact is not None
            else None
        )
        recovery_clean = all(
            row["recovery_allows_actual_token_matched_claim"] for row in arm_rows
        )
        claim_allowed = bool(
            recovery_clean
            and relative_range_exact is not None
            and relative_range_exact <= Fraction(1, 50)
        )
        e2_rate = Fraction(
            int(by_arm["E2"]["gross_known_token_lower_bound"]),
            int(by_arm["E2"]["planned_logical_calls"]),
        )
        c_rate = Fraction(
            int(by_arm["C"]["gross_known_token_lower_bound"]),
            int(by_arm["C"]["planned_logical_calls"]),
        )
        e2_tokens = float(e2_rate)
        c_tokens = float(c_rate)
        accuracy_delta = float(model_deltas[model])
        e2_c_recovery_clean = all(
            by_arm[arm]["recovery_allows_actual_token_matched_claim"]
            for arm in ("E2", "C")
        )
        if not e2_c_recovery_clean:
            pareto = "indeterminate_due_to_incomplete_gross_usage"
        elif accuracy_delta > 0.0 and e2_rate <= c_rate:
            pareto = "E2_accuracy_dominates_without_more_tokens"
        elif accuracy_delta <= 0.0 and e2_rate >= c_rate:
            pareto = "E2_accuracy_resource_dominated_or_tied"
        else:
            pareto = "accuracy_resource_tradeoff_unresolved"
        retry_clean_differences: list[int] = []
        retained_worlds: list[int] = []
        for world in range(12):
            paired = {
                run["arm_id"]: run
                for run in runs
                if run["model_stratum"] == model
                and int(run["world_index"]) == world
                and run["arm_id"] in {"C", "E2"}
            }
            if set(paired) != {"C", "E2"}:
                raise V3FinalizationError("resource E2/C world pair is incomplete")
            if all(int(item["resource"]["retry_count"]) == 0 for item in paired.values()):
                retained_worlds.append(world)
                retry_clean_differences.append(
                    int(paired["E2"]["primary_correct"])
                    - int(paired["C"]["primary_correct"])
                )
        routes.append(
            {
                "model_stratum": model,
                "arms": arm_rows,
                "realized_token_relative_range": relative_range,
                "relative_range_threshold": 0.02,
                "recovery_clean": recovery_clean,
                "actual_token_matched_claim_allowed": claim_allowed,
                "E2_C_recovery_clean": e2_c_recovery_clean,
                "E2_to_C_token_ratio": e2_tokens / c_tokens if c_tokens > 0 else None,
                "E2_minus_C_primary_score": accuracy_delta,
                "pareto_status": pareto,
                "retry_excluded_E2_vs_C": {
                    "definition": "exclude_world_pair_if_either_E2_or_C_episode_used_any_retried_slot",
                    "retained_world_indices": retained_worlds,
                    "retained_world_count": len(retained_worlds),
                    "mean_delta": (
                        None
                        if not retry_clean_differences
                        else sum(retry_clean_differences)
                        / (len(retry_clean_differences) * 64)
                    ),
                    "scope": "descriptive_only",
                },
            }
        )
    return {
        "eligible": True,
        "primary_estimand": "logical-call-matched-intention-to-treat-within-model",
        "tokens_pooled_across_model_tokenizers": False,
        "routes": routes,
        "actual_token_matched_claim_allowed_for_both_routes": all(
            route["actual_token_matched_claim_allowed"] for route in routes
        ),
        "sensitivity_required": any(
            not route["actual_token_matched_claim_allowed"] for route in routes
        ),
    }


_FORBIDDEN_PUBLIC_KEY_PARTS = (
    "expression",
    "canonical_hash",
    "behavior_hash",
    "prediction",
    "label",
    "law",
    "raw",
    "endpoint",
    "api_key",
    "authorization",
    "secret",
)


def _assert_public_snapshot_safe(
    snapshot: Mapping[str, Any], *, forbidden_values: Sequence[str] = ()
) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).lower()
                if normalized in CANDIDATE_FORMATS:
                    pass
                elif (
                    any(part in normalized for part in _FORBIDDEN_PUBLIC_KEY_PARTS)
                    or normalized == "key"
                    or normalized.endswith("_key")
                    or ("candidate" in normalized and "hash" in normalized)
                ):
                    raise V3FinalizationError(
                        f"public snapshot contains forbidden field {normalized}"
                    )
                walk(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                walk(item)

    walk(snapshot)
    encoded = canonical_json_bytes(snapshot).decode("utf-8")
    if INVALID_CANDIDATE_SENTINEL in encoded:
        raise V3FinalizationError("public snapshot retained the invalid DSL sentinel")
    if any(value and value in encoded for value in forbidden_values):
        raise V3FinalizationError("public snapshot retained private or candidate data")


def finalize_v3_campaign(
    campaign_dir: str | Path,
    *,
    output_path: str | Path | None = None,
    expected_main_entries: int = V3_EXPECTED_MAIN_EPISODES,
    test_evaluator: Callable[..., Any] | None = None,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Verify the complete generation barrier, then release V3 test endpoints.

    ``expected_main_entries`` and ``test_evaluator`` are deterministic test
    seams and are permitted only together on a reduced-cardinality fixture.
    Production requires the exact 96-episode grid, the built-in verifier, and
    an exclusive durable output path.
    """

    # Imported lazily so the generation/storage layer never imports this module
    # and therefore cannot acquire private-test capabilities by an import cycle.
    from .v3_campaign import (
        acquire_campaign_lock,
        load_campaign_manifest,
        load_episode_seal,
        load_generation_barrier,
        validate_campaign_inventory,
    )

    root = Path(campaign_dir)
    if any(not isinstance(value, str) or not value for value in forbidden_values):
        raise ValueError("forbidden_values must contain non-empty strings")
    production = expected_main_entries == V3_EXPECTED_MAIN_EPISODES
    if production and test_evaluator is not None:
        raise ValueError("production V3 forbids an injected private-test evaluator")
    if not production and test_evaluator is None:
        raise ValueError("a reduced-cardinality fixture requires a test evaluator")
    destination: Path | None
    if production:
        fixed_destination = root / V3_FINAL_SNAPSHOT_NAME
        if output_path is not None and Path(output_path).resolve() != fixed_destination.resolve():
            raise ValueError("production V3 output path is fixed inside the campaign")
        destination = fixed_destination
    else:
        destination = None if output_path is None else Path(output_path)
    if destination is not None:
        if destination.exists() or destination.is_symlink():
            # Refuse before barrier verification and, more importantly, before
            # a second private-test pass on an already finalized campaign.
            raise FileExistsError(
                f"refusing to re-finalize over existing snapshot: {destination}"
            )
    with acquire_campaign_lock(root, blocking=False):
        manifest_envelope = load_campaign_manifest(root)
        manifest = _payload(manifest_envelope, "campaign manifest")
        manifest_digest = _manifest_payload_sha256(manifest_envelope, manifest)
        entries = _main_entries(
            manifest, expected_main_entries=expected_main_entries
        )
        validate_campaign_inventory(root, manifest_envelope)

        pending: list[
            tuple[
                dict[str, Any],
                EpisodeResult,
                Mapping[str, Any],
                Mapping[str, Any],
            ]
        ] = []
        candidate_values: list[str] = []
        # Pass 1: every replay/seal check must finish before the evaluator is
        # referenced, let alone called.
        for entry in entries:
            try:
                replayed, metrics, seal_payload = _replay_episode(
                    root,
                    manifest_envelope=manifest_envelope,
                    manifest=manifest,
                    manifest_payload_sha256=manifest_digest,
                    entry=entry,
                    load_episode_seal=load_episode_seal,
                )
            except V3FinalizationError:
                raise
            except Exception as exc:
                raise V3FinalizationError(
                    f"main shard {entry.get('shard_index')} failed offline replay"
                ) from exc
            for records in replayed.rounds:
                for record in records:
                    if isinstance(record.candidate, str) and record.candidate:
                        candidate_values.append(record.candidate)
                    if record.canonical_hash:
                        candidate_values.append(record.canonical_hash)
                    if record.behavior_hash:
                        candidate_values.append(record.behavior_hash)
            pending.append((entry, replayed, metrics, seal_payload))

        # This loader recomputes the ordered 104-seal/screen inventory binding.
        # A missing 96th main seal or a late checkpoint/seal mutation therefore
        # fails while the private-test invocation count is still exactly zero.
        barrier_envelope = load_generation_barrier(root, manifest_envelope)
        barrier_payload = _payload(barrier_envelope, "generation barrier")
        validate_campaign_inventory(root, manifest_envelope)

        full_worlds: list[Any] = []
        for entry, _result, _metrics, _seal in pending:
            world = generate_world(int(entry["world_seed"]), depth=int(entry["depth"]))
            if world.world_hash != entry.get("world_hash"):
                raise V3FinalizationError("private world drifted from the frozen plan")
            if len(tuple(world.test)) != V3_TEST_POINTS_PER_EPISODE:
                raise V3FinalizationError("V3 private test must contain exactly 64 points")
            full_worlds.append(world)

        # Pass 2: only this loop can invoke a private-test evaluator.
        runs: list[dict[str, Any]] = []
        evaluator_invocations = 0
        for (entry, result, metrics, seal_payload), world in zip(
            pending, full_worlds
        ):
            verifier = Verifier(
                counterexample_limit=int(
                    manifest["frozen_config"]["episode"][
                        "max_counterexamples_per_round"
                    ]
                )
            )
            endpoint, invoked = _private_endpoint(
                result,
                metrics,
                verifier=verifier,
                world=world,
                entry=entry,
                test_evaluator=test_evaluator,
            )
            evaluator_invocations += int(invoked)
            execution_audit = seal_payload.get("execution_audit")
            if not isinstance(execution_audit, Mapping):
                raise V3FinalizationError("episode seal lacks its execution audit")
            failure_counts = dict(metrics.get("failure_counts", {}))
            runs.append(
                {
                    "shard_index": int(entry["shard_index"]),
                    "world_index": int(entry["world_index"]),
                    "world_seed": int(entry["world_seed"]),
                    "depth": int(entry["depth"]),
                    "model_stratum": str(entry["model_stratum"]),
                    "arm_id": str(entry["arm_id"]),
                    "search_valid_count": int(metrics["search_valid_count"]),
                    "canonical_unique_count": int(metrics["canonical_unique_count"]),
                    "behavioral_unique_count": int(metrics["behavioral_unique_count"]),
                    "outer_schema_valid_count": int(metrics["outer_schema_valid_count"]),
                    "selected_probe_score": metrics["selected_probe_score"],
                    "round_best_scores": list(metrics.get("round_best_scores", [])),
                    "failure_counts": failure_counts,
                    "failure_counts_by_code": dict(
                        failure_counts.get("by_code", {})
                    ),
                    "candidate_format_counts": dict(
                        metrics.get("candidate_format_counts", {})
                    ),
                    "temperature_trajectory": list(
                        metrics.get("temperature_trajectory", [])
                    ),
                    "controller_trace": list(metrics.get("controller_trace", [])),
                    "resource": {
                        name: execution_audit[name]
                        for name in (
                            "physical_request_starts",
                            "retry_count",
                            "accepted_known_input_tokens",
                            "accepted_known_output_tokens",
                            "accepted_known_latency_ms",
                            "discarded_known_response_count",
                            "discarded_known_input_tokens",
                            "discarded_known_output_tokens",
                            "discarded_known_latency_ms",
                            "usage_unknown_start_marker_count",
                            "unresolved_slot_count",
                            "exhausted_slot_count",
                            "fatal_slot_count",
                            "ready_for_retry_slot_count",
                            "gross_known_token_lower_bound",
                            "gross_known_latency_ms",
                            "gross_usage_complete",
                            "recovery_allows_actual_token_matched_claim",
                        )
                    },
                    **endpoint,
                }
            )

        aggregates = _aggregates(runs)
        reduced = expected_main_entries != V3_EXPECTED_MAIN_EPISODES
        test_only = reduced or test_evaluator is not None
        model_order = [
            str(item["stratum_id"])
            for item in manifest["frozen_config"]["model_strata"]
        ]
        classification = _classification(aggregates, test_only=test_only)
        statistical_analysis = (
            {
                "eligible": False,
                "reason": "test-only reduced cardinality",
            }
            if test_only
            else {
                "eligible": True,
                **_paired_statistical_analysis(runs, model_order),
            }
        )
        construct_diagnostics = _construct_diagnostics(
            runs, model_order, test_only=test_only
        )
        resource_sensitivity = _resource_sensitivity(
            runs,
            model_order,
            classification,
            test_only=test_only,
        )
        barrier_digest = barrier_envelope.get("payload_sha256")
        if not _is_sha256(barrier_digest):
            barrier_digest = sha256_json(barrier_payload)
        snapshot: dict[str, Any] = {
            "schema_version": 1,
            "kind": "v3-development-finalized-snapshot",
            "experiment": str(manifest["experiment"]),
            "mode": V3_FINALIZER_MODE,
            "evidence": not test_only,
            "evidence_scope": "development-only" if not test_only else "non-evidence",
            "runs": runs,
            "model_arm_aggregates": aggregates,
            "classification": classification,
            "statistical_analysis": statistical_analysis,
            "construct_diagnostics": construct_diagnostics,
            "resource_sensitivity": resource_sensitivity,
            "source": {
                "config_sha256": manifest.get("config_sha256"),
                "source_manifest_sha256": manifest.get(
                    "source_manifest_sha256"
                ),
                "execution_plan_sha256": manifest.get("execution_plan_sha256"),
                "campaign_manifest_payload_sha256": manifest_digest,
                "generation_barrier_payload_sha256": barrier_digest,
                "model_strata": [
                    {
                        "stratum_id": item["stratum_id"],
                        **(
                            {
                                "provider": item["provider"],
                                "name": item["name"],
                                "snapshot": item["snapshot"],
                                "route_binding_sha256": item["route_contract"][
                                    "route_binding_sha256"
                                ],
                            }
                            if all(
                                field in item
                                for field in (
                                    "provider",
                                    "name",
                                    "snapshot",
                                    "route_contract",
                                )
                            )
                            else {}
                        ),
                    }
                    for item in manifest["frozen_config"]["model_strata"]
                ],
            },
            "integrity": {
                "generation_barrier_verified": True,
                "manifest_payload_sha256": manifest_digest,
                "generation_barrier_payload_sha256": barrier_digest,
                "main_episode_seals_verified": len(pending),
                "logical_call_checkpoints_verified": len(pending)
                * SLOTS_PER_SHARD,
                "episodes_replayed_before_test_release": len(pending),
                "test_evaluator_invocations": evaluator_invocations,
                "expected_main_episode_count": expected_main_entries,
                "test_only_reduced_cardinality": reduced,
                "injected_test_evaluator": test_evaluator is not None,
                "network_requests": 0 if test_evaluator is None else None,
            },
        }
        all_forbidden = tuple(
            dict.fromkeys((*forbidden_values, *candidate_values))
        )
        _assert_public_snapshot_safe(snapshot, forbidden_values=all_forbidden)
        if destination is not None:
            atomic_publish_public_snapshot(
                destination,
                snapshot,
                forbidden_values=all_forbidden,
            )
        return json.loads(canonical_json_bytes(snapshot).decode("utf-8"))


# A concise alias for callers that already imported the V3-specific module.
finalize_campaign = finalize_v3_campaign


__all__ = [
    "V3_EXPECTED_MAIN_EPISODES",
    "V3_FINALIZER_MODE",
    "V3FinalizationError",
    "finalize_campaign",
    "finalize_v3_campaign",
]
