"""A small, auditable episode runner for the symbolic search experiment.

The runner owns the information boundary of an episode.  A generator sees the
training examples, the bounded archive, and verifier-released counterexamples;
it never sees probe/test labels.  Verification and world generation are kept
behind small protocols so the runner can be used with the concrete DSL/world
modules or with the deterministic smoke-test objects below.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import inspect
import json
import math
from typing import Any, Protocol, runtime_checkable

from .prompts import build_round_prompt


DEFAULT_MAX_OUTPUT_TOKENS = 256


CANDIDATE_FORMATS = frozenset(
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


def _validate_candidate_format(value: Any) -> None:
    if value is not None and (
        not isinstance(value, str) or value not in CANDIDATE_FORMATS
    ):
        raise ValueError(
            "candidate_format must be None or a member of CANDIDATE_FORMATS"
        )


@runtime_checkable
class CandidateGenerator(Protocol):
    """Protocol implemented by a model/API adapter.

    A generator must be deterministic with respect to its own seed when a
    provider supports seeding.  Extra keyword arguments are optional; the
    runner also accepts a plain callable for tiny local adapters.
    """

    def generate(
        self,
        prompt: str,
        *,
        temperature: float,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        round_index: int = 0,
        candidate_index: int = 0,
        seed: int | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> Any:
        ...


class Policy(Protocol):
    """Minimal policy surface consumed by :func:`run_episode`."""

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any]) -> float:
        ...

    def update(self, **kwargs: Any) -> Any:
        ...


@dataclass(frozen=True)
class GenerationResponse:
    """Canonical adapter envelope with auditable per-candidate usage.

    A one-shot provider adapter reports the telemetry of that accepted physical
    response, so ``provider_request_count`` is one.  A legacy adapter that owns
    an internal retry loop may instead aggregate every known billed attempt,
    but it must not claim unknown timeout usage as complete.  V3 keeps physical
    retry accounting in its separate durable execution audit and deliberately
    replays only the accepted response here. ``raw`` remains only for
    constructor compatibility; live provider adapters leave it ``None`` and no
    raw assistant content is stored or serialized.
    """

    expression: Any
    input_tokens: int
    output_tokens: int
    latency_ms: float
    provider_request_count: int = 1
    seed_supported: bool | None = None
    provider_model: str | None = None
    finish_reason: str | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    reasoning_tokens: int | None = None
    raw: Any = field(default=None, repr=False, compare=False)
    candidate_format: str | None = None
    provider_fingerprint: str | None = None

    def __post_init__(self) -> None:
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
            "reasoning_tokens",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if (
            type(self.provider_request_count) is not int
            or self.provider_request_count < 1
        ):
            raise ValueError("provider_request_count must be a positive integer")
        if not math.isfinite(float(self.latency_ms)) or self.latency_ms < 0:
            raise ValueError("latency_ms must be finite and non-negative")
        if self.seed_supported is not None and type(self.seed_supported) is not bool:
            raise ValueError("seed_supported must be bool or None")
        for name in ("provider_model", "finish_reason", "provider_fingerprint"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string or None")
        _validate_candidate_format(self.candidate_format)


@dataclass(frozen=True)
class VerificationResult:
    """Normalized verifier result used by the runner and tests."""

    score: float
    valid: bool = True
    syntax_valid: bool = True
    runtime_valid: bool = True
    predictions: tuple[Any, ...] = ()
    counterexamples: tuple[Any, ...] = ()
    failure_codes: tuple[str, ...] = ()
    error: str | None = None
    raw: Any = field(default=None, repr=False, compare=False)


@dataclass
class CandidateRecord:
    """One generated candidate and its public/probe evaluation metadata."""

    candidate: Any
    raw_response: Any
    round_index: int
    candidate_index: int
    temperature: float
    prompt: str = ""
    probe_score: float = 0.0
    syntax_valid: bool = True
    runtime_valid: bool = True
    predictions: tuple[Any, ...] = ()
    counterexamples: tuple[Any, ...] = ()
    failure_codes: tuple[str, ...] = ()
    node_count: int = 10**9
    canonical_hash: str = ""
    behavior_hash: str = ""
    error: str | None = None
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    provider_request_count: int | None = None
    seed_supported: bool | None = None
    provider_model: str | None = None
    finish_reason: str | None = None
    prompt_cache_hit_tokens: int | None = None
    prompt_cache_miss_tokens: int | None = None
    reasoning_tokens: int | None = None
    candidate_format: str | None = None
    provider_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _validate_candidate_format(self.candidate_format)
        if self.provider_fingerprint is not None and (
            not isinstance(self.provider_fingerprint, str)
            or not self.provider_fingerprint.strip()
        ):
            raise ValueError("provider_fingerprint must be a non-empty string or None")

    @property
    def score(self) -> float:
        return self.probe_score

    @property
    def actual_usage_available(self) -> bool:
        return (
            self.input_tokens is not None
            and self.output_tokens is not None
            and self.latency_ms is not None
            and self.provider_request_count is not None
        )


class Archive:
    """Bounded best-first archive with structural/behavioral deduplication."""

    def __init__(self, capacity: int = 4) -> None:
        if capacity < 1:
            raise ValueError("archive capacity must be positive")
        self.capacity = capacity
        self._entries: list[CandidateRecord] = []

    @property
    def entries(self) -> tuple[CandidateRecord, ...]:
        return tuple(self._entries)

    def __iter__(self):
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def best(self) -> CandidateRecord | None:
        return self._entries[0] if self._entries else None

    def add(self, record: CandidateRecord) -> bool:
        """Insert or replace a duplicate, preserving the strongest record."""

        # Invalid candidates are diagnostics, not hypotheses.  Keeping them in
        # the archive would let a malformed response consume a slot and would
        # make final selection depend on parser/runtime failure behavior.
        if not record.syntax_valid or not record.runtime_valid:
            return False

        key = record.behavior_hash or record.canonical_hash or _stable_hash(record.candidate)
        duplicate = next((item for item in self._entries if (item.behavior_hash or item.canonical_hash or _stable_hash(item.candidate)) == key), None)
        if duplicate is not None:
            if _rank_key(record) >= _rank_key(duplicate):
                return False
            self._entries.remove(duplicate)
        self._entries.append(record)
        self._entries.sort(key=_rank_key)
        del self._entries[self.capacity :]
        return record in self._entries

    def update(self, records: Iterable[CandidateRecord]) -> None:
        for record in records:
            self.add(record)


@dataclass
class EpisodeResult:
    """Complete episode artifact, including the private test result."""

    world: Any
    archive: Archive
    rounds: list[list[CandidateRecord]]
    counterexamples: list[Any]
    temperatures: list[float]
    final_candidate: CandidateRecord | None
    final_test: VerificationResult | None
    slot_temperatures: list[tuple[float, ...]] = field(default_factory=list)
    policy_history: list[Mapping[str, Any]] = field(default_factory=list)

    @property
    def final_test_score(self) -> float | None:
        return None if self.final_test is None else self.final_test.score

    @property
    def candidate_count(self) -> int:
        return sum(len(items) for items in self.rounds)


class FixedTemperaturePolicy:
    """Simple policy useful for baselines and smoke tests."""

    def __init__(self, temperature: float) -> None:
        self.temperature = float(temperature)
        self.history: list[Mapping[str, Any]] = []

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any]) -> float:
        return self.temperature

    def update(self, **kwargs: Any) -> None:
        self.history.append(dict(kwargs))


class AdaptiveTemperaturePolicy:
    """Reference verifier-feedback controller from the experiment spec."""

    def __init__(self, initial: float = 1.0, low: float = 0.2, high: float = 1.2) -> None:
        if not low <= initial <= high:
            raise ValueError("initial temperature must lie within [low, high]")
        self.initial = float(initial)
        self.current = self.initial
        self.low = float(low)
        self.high = float(high)
        self.history: list[Mapping[str, Any]] = []

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any]) -> float:
        return self.current

    def update(self, **kwargs: Any) -> None:
        improved = bool(kwargs.get("improved", False))
        if improved:
            self.current = max(self.low, self.current - 0.2)
        else:
            self.current = min(self.high, self.current + 0.3)
        self.history.append({**kwargs, "next_temperature": self.current})

    def reset(self) -> None:
        self.current = self.initial
        self.history.clear()


class SmokeTestGenerator:
    """Deterministic, offline candidate generator for closure tests.

    ``script`` may be a sequence of raw responses.  Once exhausted, the last
    response (or ``expression``) is repeated.  No network client is imported or
    consulted.
    """

    def __init__(
        self,
        expression: str = "(add (mul (var x1) (var x2)) (var x3))",
        script: Sequence[Any] | None = None,
    ) -> None:
        self.expression = expression
        self.script = tuple(script or ())
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        prompt: str,
        *,
        temperature: float,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        round_index: int = 0,
        candidate_index: int = 0,
        seed: int | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> Any:
        index = len(self.calls)
        response = self.script[index] if index < len(self.script) else {"expression": self.expression}
        self.calls.append(
            {
                "prompt": prompt,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "round_index": round_index,
                "candidate_index": candidate_index,
                "seed": seed,
                "state": dict(state or {}),
            }
        )
        return response


@dataclass(frozen=True)
class _SmokeExample:
    point: tuple[int, int, int]
    label: int


@dataclass(frozen=True)
class _SmokeWorld:
    train: tuple[_SmokeExample, ...]
    probe: tuple[_SmokeExample, ...]
    test: tuple[_SmokeExample, ...]


def _smoke_world() -> _SmokeWorld:
    target = lambda p: p[0] * p[1] + p[2]
    points = (
        (-2, -1, 0),
        (-1, 2, 1),
        (0, 1, -2),
        (1, 2, 0),
        (2, -1, 2),
        (2, 2, -1),
        (-2, 2, 2),
        (1, -2, -2),
    )
    examples = tuple(_SmokeExample(point, target(point)) for point in points)
    return _SmokeWorld(train=examples[:4], probe=examples[4:6], test=examples[6:])


def run_smoke_episode() -> EpisodeResult:
    """Run a complete five-round/four-call offline episode."""

    return run_episode(
        _smoke_world(),
        SmokeTestGenerator(),
        verifier=None,
        policy=_make_default_policy(),
    )


def run_episode(
    world: Any,
    candidate_generator: CandidateGenerator | Callable[..., Any] | None = None,
    verifier: Any | None = None,
    policy: Any | None = None,
    *,
    generator: CandidateGenerator | Callable[..., Any] | None = None,
    rounds: int = 5,
    candidates_per_round: int = 4,
    archive_capacity: int = 4,
    max_counterexamples: int | None = None,
    seed: int | None = 0,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_counterexamples_per_round: int = 2,
    evaluate_test: bool = True,
) -> EpisodeResult:
    """Run one fixed-budget episode.

    Exactly ``rounds * candidates_per_round`` generation calls are made; no
    early stopping is allowed.  The default values implement the preregistered
    five-round by four-candidate protocol.
    """

    if candidate_generator is None:
        candidate_generator = generator
    elif generator is not None:
        raise TypeError("pass either candidate_generator or generator, not both")
    if candidate_generator is None:
        raise TypeError("candidate_generator is required")
    if rounds < 1 or candidates_per_round < 1:
        raise ValueError("rounds and candidates_per_round must be positive")
    if max_output_tokens < 1:
        raise ValueError("max_output_tokens must be positive")
    if max_counterexamples_per_round < 0:
        raise ValueError("max_counterexamples_per_round cannot be negative")
    if max_counterexamples is not None and max_counterexamples < 0:
        raise ValueError("max_counterexamples cannot be negative")
    if type(evaluate_test) is not bool:
        raise TypeError("evaluate_test must be a boolean")
    total_counterexample_limit = (
        rounds * max_counterexamples_per_round
        if max_counterexamples is None
        else max_counterexamples
    )
    if policy is None:
        policy = _make_default_policy()
    else:
        reset = getattr(policy, "reset", None)
        if callable(reset):
            reset()
    if verifier is None:
        from .verifier import Verifier

        verifier = Verifier()

    archive = Archive(archive_capacity)
    round_records: list[list[CandidateRecord]] = []
    released_counterexamples: list[Any] = []
    temperatures: list[float] = []
    slot_temperatures: list[tuple[float, ...]] = []
    policy_history: list[Mapping[str, Any]] = []
    # State is intentionally a small public score summary.  In particular it
    # never contains archive records, raw model responses, or unreleased probe
    # counterexamples; those objects must not cross the generator/policy
    # boundary even though they remain available in the returned episode log.
    state: dict[str, Any] = {"best_probe_score": 0.0, "round": 0, "improved": False}
    best_score = 0.0
    slot_bests: list[CandidateRecord | None] = [None] * candidates_per_round
    seen_valid_behavior_hashes: set[str] = set()

    for round_index in range(rounds):
        round_temperatures = _policy_temperatures(
            policy, round_index, dict(state), candidates_per_round
        )
        slot_temperatures.append(round_temperatures)
        # Preserve the convenient scalar history for single-temperature arms;
        # the complete per-slot schedule is available in slot_temperatures.
        temperatures.append(round_temperatures[0])
        # A scalar/single-temperature arm gets one identical prompt per slot.
        # A multi-temperature arm gets the global elite plus that slot's own
        # historical best, while preserving the fixed archive context bound.
        multi_slot = (
            len(set(round_temperatures)) > 1
            or str(getattr(policy, "arm_id", "")).upper() == "MTX"
        )
        global_archive = list(archive.entries)
        records: list[CandidateRecord] = []
        for candidate_index in range(candidates_per_round):
            prompt_archive = (
                _slot_prompt_archive(
                    global_archive,
                    slot_bests[candidate_index],
                    capacity=archive_capacity,
                )
                if multi_slot
                else global_archive[:archive_capacity]
            )
            prompt = build_round_prompt(
                world,
                round_index=round_index,
                archive=prompt_archive,
                counterexamples=released_counterexamples,
                # Sampling temperature is intentionally not rendered.
                temperature=round_temperatures[candidate_index],
                archive_capacity=archive_capacity,
            )
            call_seed = None if seed is None else seed + round_index * candidates_per_round + candidate_index
            raw = _call_generator(
                candidate_generator,
                prompt,
                temperature=round_temperatures[candidate_index],
                round_index=round_index,
                candidate_index=candidate_index,
                seed=call_seed,
                max_output_tokens=max_output_tokens,
                # Give each adapter a fresh copy so a mutable integration
                # cannot alter the state visible to a later slot.
                state=dict(state),
            )
            record = _score_record(
                raw,
                prompt=prompt,
                world=world,
                verifier=verifier,
                split="probe",
                round_index=round_index,
                candidate_index=candidate_index,
                temperature=round_temperatures[candidate_index],
                max_output_tokens=max_output_tokens,
            )
            records.append(record)
        round_records.append(records)
        archive.update(records)
        for slot_index, record in enumerate(records):
            previous = slot_bests[slot_index]
            if record.syntax_valid and record.runtime_valid and (
                previous is None or _rank_key(record) < _rank_key(previous)
            ):
                slot_bests[slot_index] = record

        valid_records = [record for record in records if record.syntax_valid and record.runtime_valid]
        pre_round_best_score = best_score
        validity_novelty_observation: dict[str, Any] | None = None
        if bool(
            getattr(policy, "requires_validity_novelty_observation", False)
        ):
            validity_novelty_observation = _validity_novelty_observation(
                policy,
                valid_records,
                seen_valid_behavior_hashes,
                pre_round_best_score=pre_round_best_score,
                planned_candidate_count=candidates_per_round,
            )
        round_best = max((record.probe_score for record in valid_records), default=0.0)
        # A round made entirely of invalid responses must not count as progress
        # merely because its default score is numerically equal to the initial
        # score.
        improved = bool(valid_records) and round_best > best_score
        if improved:
            best_score = round_best
        state = {
            "round": round_index + 1,
            "best_probe_score": best_score,
            "improved": improved,
        }
        selected = archive.best()
        new_feedback = list(selected.counterexamples if selected else ())
        released_this_round = 0
        if max_counterexamples_per_round:
            for item in new_feedback:
                if not _contains_equivalent(released_counterexamples, item):
                    released_counterexamples.append(item)
                    released_this_round += 1
                    if released_this_round >= max_counterexamples_per_round:
                        break
        del released_counterexamples[total_counterexample_limit:]
        policy_observation: dict[str, Any] = {
            "round_index": round_index,
            "round_best": round_best,
            "best_score": best_score,
            "improved": improved,
        }
        if validity_novelty_observation is not None:
            policy_observation.update(validity_novelty_observation)
        if str(getattr(policy, "arm_id", "")).upper() == "MTX":
            # Only MTX owns an elite-exchange state. Other controllers receive
            # scores alone, preserving the frozen rule that E cannot inspect
            # candidate/task identity. Even here, pass only the public AST,
            # never the record containing raw output or private probe data.
            policy_observation["elite"] = (
                None if selected is None else selected.candidate
            )
        update = _policy_update(policy, **policy_observation)
        if update is not None:
            policy_history.append(_as_mapping(update))
        elif hasattr(policy, "history") and policy.history:
            policy_history.append(_as_mapping(policy.history[-1]))

    final_candidate = archive.best()
    result = EpisodeResult(
        world=world,
        archive=archive,
        rounds=round_records,
        counterexamples=released_counterexamples,
        temperatures=temperatures,
        slot_temperatures=slot_temperatures,
        final_candidate=final_candidate,
        final_test=None,
        policy_history=policy_history,
    )
    if evaluate_test:
        result.final_test = evaluate_episode_test(result, verifier=verifier)
    return result


def evaluate_episode_test(
    result: EpisodeResult,
    *,
    verifier: Any | None = None,
) -> VerificationResult | None:
    """Evaluate an episode's selected candidate on its private test exactly once.

    The experiment orchestrator calls this only after every configured model
    generation has completed.  The standalone episode API defaults to the
    historical immediate-finalization behavior for backwards compatibility.
    """

    if not isinstance(result, EpisodeResult):
        raise TypeError("result must be an EpisodeResult")
    if result.final_test is not None:
        raise ValueError("episode final test has already been evaluated")
    if result.final_candidate is None:
        return None
    return _verify_candidate(
        result.final_candidate.candidate,
        result.world,
        verifier,
        split="test",
    )


def _policy_temperatures(
    policy: Any,
    round_index: int,
    state: Mapping[str, Any],
    slot_count: int,
) -> tuple[float, ...]:
    """Resolve one temperature per candidate slot.

    Multi-temperature policies may expose ``temperatures_for_round`` or return
    a sequence from ``temperature_for_round``.  Scalar policies are replicated
    across slots, preserving the fixed-budget protocol.
    """

    value: Any = None
    for name in ("temperatures_for_round", "temperatures_at"):
        method = getattr(policy, name, None)
        if callable(method):
            try:
                value = method(round_index, state)
            except TypeError:
                value = method(round_index)
            break
    if value is None:
        for name in ("temperature_for_round", "get_temperature", "temperature_at"):
            method = getattr(policy, name, None)
            if callable(method):
                try:
                    value = method(round_index, state)
                except TypeError:
                    value = method(round_index)
                break
    if value is None:
        value = getattr(policy, "temperature", None)
        if callable(value):
            value = value(round_index)
    if value is None and callable(policy):
        value = policy(round_index, state)
    if value is None:
        raise TypeError("policy must expose a temperature method or value")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = tuple(float(item) for item in value)
        if len(values) == 1:
            return values * slot_count
        if len(values) != slot_count:
            raise ValueError(
                f"policy returned {len(values)} temperatures for {slot_count} slots"
            )
        return values
    return (float(value),) * slot_count


def _policy_temperature(policy: Any, round_index: int, state: Mapping[str, Any]) -> float:
    """Compatibility helper for callers that only need one slot."""

    return _policy_temperatures(policy, round_index, state, 1)[0]


def _validity_novelty_observation(
    policy: Any,
    valid_records: Sequence[CandidateRecord],
    seen_valid_behavior_hashes: set[str],
    *,
    pre_round_best_score: float,
    planned_candidate_count: int,
) -> dict[str, Any]:
    """Return the scalar-only E2 observation and advance runner-owned novelty state.

    Behavioral identities are used only inside this helper.  Neither the hash
    values nor candidate records cross the policy boundary.  Missing behavior
    hashes fail closed because silently substituting structural novelty would
    change the declared E2 treatment.
    """

    tolerance = float(getattr(policy, "useful_novelty_score_tolerance"))
    if not math.isfinite(tolerance) or not 0.0 <= tolerance <= 1.0:
        raise ValueError(
            "policy useful_novelty_score_tolerance must lie within [0, 1]"
        )
    behavior_best_scores: dict[str, float] = {}
    for record in valid_records:
        behavior_hash = record.behavior_hash
        if not isinstance(behavior_hash, str) or not behavior_hash:
            raise ValueError(
                "E2 requires a non-empty behavior hash for every valid candidate"
            )
        behavior_best_scores[behavior_hash] = max(
            float(record.probe_score),
            behavior_best_scores.get(behavior_hash, float("-inf")),
        )
    new_behavior_hashes = set(behavior_best_scores) - seen_valid_behavior_hashes
    useful_new_behavior_count = sum(
        behavior_best_scores[behavior_hash] > 0.0
        and behavior_best_scores[behavior_hash]
        >= pre_round_best_score - tolerance
        for behavior_hash in new_behavior_hashes
    )
    seen_valid_behavior_hashes.update(behavior_best_scores)
    return {
        "pre_round_best_score": float(pre_round_best_score),
        "planned_candidate_count": int(planned_candidate_count),
        "valid_candidate_count": len(valid_records),
        "new_behavior_count": len(new_behavior_hashes),
        "useful_new_behavior_count": useful_new_behavior_count,
    }


def _make_default_policy() -> Any:
    """Use the experiment policy module when available.

    The local class remains as an import-light fallback for isolated smoke
    tests, but real runs should share the canonical policy implementation and
    its configuration constants.
    """

    try:
        from .policies import AdaptiveTemperaturePolicy as CanonicalAdaptive

        return CanonicalAdaptive()
    except (ImportError, AttributeError):
        return AdaptiveTemperaturePolicy()


def _policy_update(policy: Any, **kwargs: Any) -> Any:
    method = getattr(policy, "update", None)
    if not callable(method):
        method = getattr(policy, "observe", None)
    if not callable(method):
        return None
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        # Unknown signatures get one call only.  A TypeError raised inside the
        # policy is not treated as a request to retry with fewer arguments.
        return method(**kwargs)
    parameters = signature.parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return method(**kwargs)
    accepted = {
        name
        for name, parameter in parameters.items()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return method(**{name: value for name, value in kwargs.items() if name in accepted})


def _call_generator(generator: Any, prompt: str, **kwargs: Any) -> Any:
    method = getattr(generator, "generate", None)
    if not callable(method):
        method = generator
    if not callable(method):
        raise TypeError("candidate_generator must be callable or expose generate")
    # Filter optional adapter arguments from its signature once.  Retrying a
    # call after catching TypeError is unsafe: the exception may have originated
    # inside a real provider after it already consumed a request, causing a
    # duplicate external generation for the same slot.
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        # Some extension/builtin callables do not expose a signature.  Make one
        # best-effort call and let any TypeError propagate unchanged.
        return method(prompt, **kwargs)

    parameters = signature.parameters
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_var_kwargs:
        call_kwargs = dict(kwargs)
    else:
        accepted = {
            name
            for name, parameter in parameters.items()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        call_kwargs = {name: value for name, value in kwargs.items() if name in accepted}
    return method(prompt, **call_kwargs)


def _score_record(
    raw: Any,
    *,
    prompt: str,
    world: Any,
    verifier: Any | None,
    split: str,
    round_index: int,
    candidate_index: int,
    temperature: float,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
) -> CandidateRecord:
    usage = _generation_usage(raw)
    candidate, syntax_valid, parse_error = _extract_candidate(raw)
    if not syntax_valid:
        return CandidateRecord(
            candidate=candidate,
            raw_response=raw,
            prompt=prompt,
            round_index=round_index,
            candidate_index=candidate_index,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            syntax_valid=False,
            runtime_valid=False,
            failure_codes=("parse_or_grammar",),
            error=parse_error,
            canonical_hash=_stable_hash(candidate),
            **usage,
        )
    result = _verify_candidate(candidate, world, verifier, split=split)
    node_count = _node_count(candidate)
    canonical = _canonical_hash(candidate)
    behavior = _behavior_hash(candidate, world)
    syntax_flag = bool(_read(result, "syntax_valid", default=True))
    runtime_flag = bool(_read(result, "runtime_valid", default=result.valid))
    # Some adapters expose only ``valid=False`` and leave the more specific
    # flags at their dataclass defaults.  Treat the aggregate failure as a
    # runtime-invalid record so it cannot consume an archive slot.
    if not bool(_read(result, "valid", default=syntax_flag and runtime_flag)):
        runtime_flag = False
    return CandidateRecord(
        candidate=candidate,
        raw_response=raw,
        prompt=prompt,
        round_index=round_index,
        candidate_index=candidate_index,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        probe_score=result.score,
        syntax_valid=syntax_flag,
        runtime_valid=runtime_flag,
        predictions=result.predictions,
        counterexamples=result.counterexamples,
        failure_codes=result.failure_codes,
        node_count=node_count,
        canonical_hash=canonical,
        behavior_hash=behavior,
        error=result.error,
        **usage,
    )


def _generation_usage(raw: Any) -> dict[str, Any]:
    """Extract usage only from the canonical, validated adapter envelope."""

    if not isinstance(raw, GenerationResponse):
        return {
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": None,
            "provider_request_count": None,
            "seed_supported": None,
            "provider_model": None,
            "finish_reason": None,
            "prompt_cache_hit_tokens": None,
            "prompt_cache_miss_tokens": None,
            "reasoning_tokens": None,
            "candidate_format": None,
            "provider_fingerprint": None,
        }
    return {
        "input_tokens": raw.input_tokens,
        "output_tokens": raw.output_tokens,
        "latency_ms": float(raw.latency_ms),
        "provider_request_count": raw.provider_request_count,
        "seed_supported": raw.seed_supported,
        "provider_model": raw.provider_model,
        "finish_reason": raw.finish_reason,
        "prompt_cache_hit_tokens": raw.prompt_cache_hit_tokens,
        "prompt_cache_miss_tokens": raw.prompt_cache_miss_tokens,
        "reasoning_tokens": raw.reasoning_tokens,
        "candidate_format": raw.candidate_format,
        "provider_fingerprint": raw.provider_fingerprint,
    }


def _extract_candidate(raw: Any) -> tuple[Any, bool, str | None]:
    value = raw
    if isinstance(value, Mapping):
        value = value.get("expression", value.get("candidate", value.get("ast", value)))
    else:
        for name in ("expression", "candidate", "ast"):
            if hasattr(value, name):
                value = getattr(value, name)
                break
    if isinstance(value, Mapping):
        return value, False, "candidate response did not contain an expression"
    if not isinstance(value, str):
        return value, True, None
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            text = str(payload.get("expression", payload.get("candidate", ""))).strip()
        except (ValueError, TypeError, AttributeError) as exc:
            return value, False, f"invalid JSON candidate: {exc}"
    try:
        from .dsl import parse_sexpr

        return parse_sexpr(text), True, None
    except ImportError:
        return text, True, None
    except Exception as exc:
        return value, False, f"invalid DSL candidate: {exc}"


def _verify_candidate(candidate: Any, world: Any, verifier: Any | None, *, split: str) -> VerificationResult:
    examples = _split_examples(world, split)
    if verifier is not None:
        preferred = "verify_test" if split == "test" else "verify_probe"
        method = getattr(verifier, preferred, None)
        if callable(method):
            result = _call_verifier(
                method,
                candidate,
                world,
                examples,
                split,
                counterexample_limit=len(examples) if split == "probe" else 0,
            )
            return _coerce_verification(result, candidate, examples)
        for name in ("evaluate", "verify", "score"):
            method = getattr(verifier, name, None)
            if callable(method):
                result = _call_verifier(
                    method,
                    candidate,
                    world,
                    examples,
                    split,
                    counterexample_limit=len(examples) if split == "probe" else 0,
                )
                return _coerce_verification(result, candidate, examples)
        if callable(verifier):
            result = _call_verifier(
                verifier,
                candidate,
                world,
                examples,
                split,
                counterexample_limit=len(examples) if split == "probe" else 0,
            )
            return _coerce_verification(result, candidate, examples)
    return _fallback_verification(candidate, examples)


def _call_verifier(
    method: Callable[..., Any],
    candidate: Any,
    world: Any,
    examples: Sequence[Any],
    split: str,
    *,
    counterexample_limit: int | None = None,
) -> Any:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(candidate, examples)

    parameters = signature.parameters
    accepts_var_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    args: list[Any] = [candidate]
    kwargs: dict[str, Any] = {}

    data_name = next(
        (name for name in ("points", "examples", "world") if name in parameters),
        None,
    )
    if data_name is not None:
        parameter = parameters[data_name]
        data_value = world if data_name == "world" else examples
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            args.append(data_value)
        else:
            kwargs[data_name] = data_value
    else:
        positional = [
            parameter
            for parameter in parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) >= 2:
            args.append(examples)

    if "split" in parameters:
        kwargs["split"] = split
    if counterexample_limit is not None and (
        "counterexample_limit" in parameters or accepts_var_kwargs
    ):
        kwargs["counterexample_limit"] = counterexample_limit
    return method(*args, **kwargs)


def _coerce_verification(result: Any, candidate: Any, examples: Sequence[Any]) -> VerificationResult:
    if isinstance(result, VerificationResult):
        return result
    score = _read(
        result,
        "score",
        "probe_score",
        "probe_accuracy",
        "accuracy",
        "fraction_correct",
        default=None,
    )
    syntax_valid = bool(_read(result, "syntax_valid", default=True))
    runtime_valid = bool(_read(result, "runtime_valid", default=True))
    valid = bool(_read(result, "valid", default=(syntax_valid and runtime_valid)))
    predictions = _read(result, "predictions", "outputs", default=())
    counterexamples = _read(result, "counterexamples", "failures", default=())
    failure_codes = _read(
        result,
        "failure_codes",
        "failure_types",
        "codes",
        "failure_categories",
        default=(),
    )
    error = _read(result, "error", "message", default=None)
    if score is None and isinstance(result, (int, float)):
        score = float(result)
    if score is None and isinstance(result, Mapping):
        correct = result.get("correct")
        if correct is not None and examples:
            score = float(sum(bool(x) for x in correct)) / len(examples)
    if score is None:
        score = 0.0
    try:
        score = float(score)
    except (TypeError, ValueError):
        score = 0.0
    if not counterexamples and predictions:
        counterexamples = _mismatches(examples, predictions)
    return VerificationResult(
        score=max(0.0, min(1.0, score)),
        valid=valid,
        syntax_valid=syntax_valid,
        runtime_valid=runtime_valid,
        predictions=tuple(predictions or ()),
        counterexamples=tuple(counterexamples or ()),
        failure_codes=tuple(str(code) for code in (failure_codes or ())),
        error=None if error is None else str(error),
        raw=result,
    )


def _fallback_verification(candidate: Any, examples: Sequence[Any]) -> VerificationResult:
    predictions: list[Any] = []
    failures: list[Any] = []
    try:
        from .dsl import evaluate
    except ImportError:
        evaluate = None
    if evaluate is None:
        return VerificationResult(score=1.0 if candidate is not None else 0.0, valid=candidate is not None)
    for example in examples:
        point, label = _point_label(example)
        try:
            prediction = evaluate(candidate, {"x1": point[0], "x2": point[1], "x3": point[2]})
            predictions.append(prediction)
            if prediction != label:
                failures.append(example)
        except Exception as exc:
            return VerificationResult(
                score=0.0,
                valid=False,
                runtime_valid=False,
                predictions=tuple(predictions),
                failure_codes=("runtime",),
                error=str(exc),
            )
    score = 1.0 if not examples else (len(examples) - len(failures)) / len(examples)
    return VerificationResult(score=score, valid=True, predictions=tuple(predictions), counterexamples=tuple(failures))


def _split_examples(world: Any, split: str) -> tuple[Any, ...]:
    names = {
        "train": ("train", "train_examples", "x_train"),
        "probe": ("probe", "probe_examples", "x_probe"),
        "test": ("test", "test_examples", "x_test"),
    }
    values = _read(world, *names.get(split, (split,)), default=None)
    if values is not None:
        values = tuple(values)
        # Support worlds that expose x_* and y_* separately.
        if split.startswith("x_") or (values and not _has_label(values[0])):
            labels = _read(world, f"y_{split}", f"{split}_labels", default=None)
            if labels is not None:
                return tuple((point, label) for point, label in zip(values, labels))
        return values
    return ()


def _point_label(example: Any) -> tuple[tuple[int, int, int], Any]:
    if isinstance(example, Mapping):
        point = example.get("point", example.get("inputs", example.get("input", example.get("x"))))
        label = example.get("label", example.get("output", example.get("target", example.get("y"))))
    elif isinstance(example, (tuple, list)) and len(example) == 2:
        point, label = example
    else:
        point = getattr(example, "point", getattr(example, "inputs", getattr(example, "x", None)))
        label = getattr(example, "label", getattr(example, "output", getattr(example, "y", None)))
    if point is None or label is None:
        raise ValueError(f"example lacks point/label fields: {example!r}")
    return tuple(int(x) for x in point), label


def _mismatches(examples: Sequence[Any], predictions: Sequence[Any]) -> tuple[Any, ...]:
    failures = []
    for example, prediction in zip(examples, predictions):
        try:
            if prediction != _point_label(example)[1]:
                failures.append(example)
        except Exception:
            failures.append(example)
    return tuple(failures)


def _has_label(value: Any) -> bool:
    return _read(value, "label", "output", "target", "y", default=None) is not None


def _node_count(candidate: Any) -> int:
    try:
        from .dsl import node_count

        return int(node_count(candidate))
    except Exception:
        return 10**9


def _canonical_hash(candidate: Any) -> str:
    try:
        from .dsl import canonical_hash

        value = canonical_hash(candidate)
        return str(value)
    except Exception:
        return _stable_hash(candidate)


def _behavior_hash(candidate: Any, world: Any) -> str:
    try:
        from .dsl import behavior_hash

        return str(behavior_hash(candidate))
    except Exception:
        try:
            from .dsl import evaluate

            domain = _read(world, "domain", default=None)
            if domain is not None:
                values = []
                for point in domain:
                    values.append(evaluate(candidate, {"x1": point[0], "x2": point[1], "x3": point[2]}))
                return _stable_hash(values)
        except Exception:
            pass
        return ""


def _rank_key(record: CandidateRecord) -> tuple[float, int, str]:
    """Frozen ranking: score descending, node count ascending, hash ascending."""

    return (-float(record.probe_score), int(record.node_count), str(record.canonical_hash))


def _read(value: Any, *names: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _stable_hash(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = repr(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {"value": value}


def _contains_equivalent(items: Sequence[Any], candidate: Any) -> bool:
    candidate_key = _feedback_key(candidate)
    return any(_feedback_key(item) == candidate_key for item in items)


def _feedback_key(value: Any) -> tuple[str, str]:
    """Return a stable key for released counterexample de-duplication.

    A verifier may report the same probe point more than once with a different
    ``predicted`` value as candidate hypotheses change.  The protocol releases
    each input point only once, so prediction is deliberately excluded.  The
    expected label is included when available to guard against malformed
    heterogeneous feedback records while retaining point-based semantics.
    """

    point = _read(value, "inputs", "point", "input", "x", default=None)
    if point is None:
        return ("raw", _stable_hash(value))
    expected_marker = object()
    expected = _read(
        value,
        "expected",
        "label",
        "output",
        "target",
        "y",
        default=expected_marker,
    )
    payload: Any = (point, None if expected is expected_marker else expected)
    return ("point", _stable_hash(payload))


def _same_candidate(left: CandidateRecord, right: CandidateRecord) -> bool:
    left_key = left.behavior_hash or left.canonical_hash or _stable_hash(left.candidate)
    right_key = right.behavior_hash or right.canonical_hash or _stable_hash(right.candidate)
    return left_key == right_key


def _slot_prompt_archive(
    global_archive: Sequence[CandidateRecord],
    slot_best: CandidateRecord | None,
    *,
    capacity: int,
) -> list[CandidateRecord]:
    """Build a bounded MTX context containing the shared and local elites."""

    if capacity < 1:
        return []
    context = list(global_archive[:capacity])
    if slot_best is None or any(_same_candidate(slot_best, item) for item in context):
        return context
    if capacity == 1:
        # The bound makes it impossible to show both; global elite wins.
        return context
    # Keep the global elite and replace the weakest contextual item with this
    # stream's own best.  Sorting restores the frozen archive order.
    context = context[: capacity - 1] + [slot_best]
    deduped: list[CandidateRecord] = []
    for item in context:
        if not any(_same_candidate(item, seen) for seen in deduped):
            deduped.append(item)
    deduped.sort(key=_rank_key)
    return deduped[:capacity]


__all__ = [
    "AdaptiveTemperaturePolicy",
    "Archive",
    "CANDIDATE_FORMATS",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "CandidateGenerator",
    "CandidateRecord",
    "EpisodeResult",
    "FixedTemperaturePolicy",
    "GenerationResponse",
    "Policy",
    "SmokeTestGenerator",
    "VerificationResult",
    "evaluate_episode_test",
    "run_episode",
    "run_smoke_episode",
]
