"""Configuration validation and orchestration for matched-budget episodes.

This module is deliberately provider agnostic.  A caller injects one candidate
generator per world/arm run through a factory, while this layer owns frozen
world creation, policy construction, budget arguments, and JSON-safe summaries.

The ordinary factory boundary receives :class:`GeneratorContext`, never a
``SyntheticWorld``.  In particular, the hidden law and private test examples
are not factory inputs and are not copied into the returned summary.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import inspect
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .policies import (
    ADAPTIVE_CONTROLLER_E2,
    ADAPTIVE_CONTROLLER_V1,
    ADAPTIVE_DECREASE,
    ADAPTIVE_INCREASE,
    ADAPTIVE_INITIAL,
    ARM_IDS,
    E2_DECISION_PRECEDENCE,
    HIGH_TEMPERATURE,
    LOW_TEMPERATURE,
    SCHEDULES,
    build_policy,
)
from .runner import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    EpisodeResult,
    SmokeTestGenerator,
    evaluate_episode_test,
    run_episode,
)
from .verifier import Verifier
from .world_generator import DEFAULT_DEPTH_TIERS, generate_world


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "pilot.json"
DEVELOPMENT_SEED_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "development-seed-registry.json"
)
SUMMARY_SCHEMA_VERSION = 1
# Deliberately independent of config, world, and arm identity. Adapters see the
# derived per-call seeds, so a config-derived seed would become an enumerable
# side channel back to the private procedural world.
SAMPLING_BASE_SEED = 1729
# A frozen cyclic order counterbalances provider-time drift across worlds.  One
# complete block of seven worlds places every treatment in every serial
# position exactly once.  The first row also separates fixed low/high before
# the adaptive and open-loop schedules in the one-world development gate.
ARM_EXECUTION_BASE_ORDER = ("L", "H", "M", "MTX", "E", "A", "C")


class ConfigError(ValueError):
    """Raised when an experiment configuration cannot be run safely."""


@dataclass(frozen=True)
class GeneratorContext:
    """Public metadata available while constructing one candidate generator.

    No run-, world-, or arm-specific identifier is present.  Seed, depth, world
    hash, generated world object, hidden law, probe labels, and test examples are
    withheld because the public procedural generator would make identifiers
    enumerable back to private worlds.  Arm identity and config are withheld so
    temperature is the only treatment visible to the adapter.  Model adapters
    should fix their provider/model and output-token limit from this context,
    then let :func:`src.runner.run_episode` supply prompts and temperatures.
    """

    experiment: str
    episode: Mapping[str, Any]
    model: Mapping[str, Any]
    max_output_tokens: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe copy suitable for adapter-side logging."""

        return {
            "experiment": self.experiment,
            "episode": dict(self.episode),
            "model": dict(self.model),
            "max_output_tokens": self.max_output_tokens,
        }


GeneratorFactory = Callable[..., Any]


class OfflineSmokeGeneratorFactory:
    """Offline-only plumbing factory; its output is never scientific evidence.

    The fixed candidate does not inspect a generated world and makes no network
    calls.  The orchestrator detects the ``evidence`` marker and unconditionally
    labels the complete output ``evidence=false``.
    """

    evidence = False
    offline_only = True
    mode = "offline-smoke"
    evidence_reason = "fixed offline smoke generator; plumbing check only"

    def __init__(self, expression: str = "(var x1)") -> None:
        self.expression = expression

    def __call__(self, context: GeneratorContext) -> SmokeTestGenerator:
        del context
        return SmokeTestGenerator(expression=self.expression)


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"configuration must be JSON serializable: {exc}") from exc


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field} must be an object")
    return value


def _integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise ConfigError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{field} must be >= {minimum}")
    return value


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ConfigError(f"{field} must be finite and >= {minimum}")
    return result


def _required(mapping: Mapping[str, Any], key: str, parent: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"{parent}.{key} is required")
    return mapping[key]


def _temperatures(value: Any, field: str) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ConfigError(f"{field} must be an array")
    result = tuple(_number(item, f"{field}[{index}]") for index, item in enumerate(value))
    if not result:
        raise ConfigError(f"{field} cannot be empty")
    return result


def _validate_arm(
    arm_id: str,
    raw_spec: Any,
    *,
    rounds: int,
    candidates_per_round: int,
) -> None:
    spec = _mapping(raw_spec, f"arms.{arm_id}")
    kind = _required(spec, "kind", f"arms.{arm_id}")
    expected_kinds = {
        "L": "fixed",
        "M": "fixed",
        "H": "fixed",
        "A": "sequence",
        "C": "sequence",
        "MTX": "multi",
        "E": "adaptive",
        "E2": "adaptive",
    }
    if kind != expected_kinds[arm_id]:
        raise ConfigError(f"arms.{arm_id}.kind must be {expected_kinds[arm_id]!r}")

    if kind == "fixed":
        temperature = _number(
            _required(spec, "temperature", f"arms.{arm_id}"),
            f"arms.{arm_id}.temperature",
        )
        expected = SCHEDULES[arm_id][0]
        if temperature != expected:
            raise ConfigError(
                f"arms.{arm_id}.temperature must be {expected}; build_policy freezes this arm"
            )
        return

    if kind == "sequence":
        values = _temperatures(
            _required(spec, "temperatures", f"arms.{arm_id}"),
            f"arms.{arm_id}.temperatures",
        )
        if len(values) != rounds:
            raise ConfigError(f"arms.{arm_id}.temperatures must contain one value per round")
        return

    if kind == "multi":
        values = _temperatures(
            _required(spec, "temperatures", f"arms.{arm_id}"),
            f"arms.{arm_id}.temperatures",
        )
        if len(values) != candidates_per_round:
            raise ConfigError(
                f"arms.{arm_id}.temperatures must contain one value per candidate slot"
            )
        return

    initial = _number(
        _required(spec, "initial_temperature", f"arms.{arm_id}"),
        f"arms.{arm_id}.initial_temperature",
    )
    low = _number(
        _required(spec, "minimum_temperature", f"arms.{arm_id}"),
        f"arms.{arm_id}.minimum_temperature",
    )
    high = _number(
        _required(spec, "maximum_temperature", f"arms.{arm_id}"),
        f"arms.{arm_id}.maximum_temperature",
    )
    improvement = _required(spec, "improvement_step", f"arms.{arm_id}")
    stagnation = _required(spec, "stagnation_step", f"arms.{arm_id}")
    if isinstance(improvement, bool) or not isinstance(improvement, (int, float)):
        raise ConfigError(f"arms.{arm_id}.improvement_step must be a negative number")
    if not math.isfinite(float(improvement)) or float(improvement) >= 0:
        raise ConfigError(f"arms.{arm_id}.improvement_step must be a finite negative number")
    _number(stagnation, f"arms.{arm_id}.stagnation_step", minimum=0.0)
    if float(stagnation) <= 0:
        raise ConfigError(f"arms.{arm_id}.stagnation_step must be positive")
    if not low <= initial <= high:
        raise ConfigError(f"arms.{arm_id} temperatures must satisfy minimum <= initial <= maximum")
    default_controller_version = (
        ADAPTIVE_CONTROLLER_E2 if arm_id == "E2" else ADAPTIVE_CONTROLLER_V1
    )
    controller_version = spec.get(
        "controller_version",
        default_controller_version,
    )
    if arm_id == "E2" and controller_version != ADAPTIVE_CONTROLLER_E2:
        raise ConfigError(
            f"arms.E2.controller_version must be {ADAPTIVE_CONTROLLER_E2!r}"
        )
    if controller_version not in {ADAPTIVE_CONTROLLER_V1, ADAPTIVE_CONTROLLER_E2}:
        raise ConfigError(
            f"arms.{arm_id}.controller_version must be "
            f"{ADAPTIVE_CONTROLLER_V1!r} or {ADAPTIVE_CONTROLLER_E2!r}"
        )
    e2_fields = (
        "minimum_valid_candidates",
        "minimum_useful_new_behaviors",
        "useful_novelty_score_tolerance",
        "decision_precedence",
    )
    if controller_version == ADAPTIVE_CONTROLLER_V1:
        unexpected = [field for field in e2_fields if field in spec]
        if unexpected:
            raise ConfigError(
                f"arms.{arm_id} E2 fields require "
                f"controller_version={ADAPTIVE_CONTROLLER_E2!r}: {unexpected}"
            )
        return
    minimum_valid = _integer(
        _required(spec, "minimum_valid_candidates", f"arms.{arm_id}"),
        f"arms.{arm_id}.minimum_valid_candidates",
        minimum=1,
    )
    if minimum_valid > candidates_per_round:
        raise ConfigError(
            f"arms.{arm_id}.minimum_valid_candidates cannot exceed "
            "episode.candidates_per_round"
        )
    minimum_useful = _integer(
        _required(spec, "minimum_useful_new_behaviors", f"arms.{arm_id}"),
        f"arms.{arm_id}.minimum_useful_new_behaviors",
        minimum=1,
    )
    if minimum_useful > candidates_per_round:
        raise ConfigError(
            f"arms.{arm_id}.minimum_useful_new_behaviors cannot exceed "
            "episode.candidates_per_round"
        )
    tolerance = _number(
        _required(spec, "useful_novelty_score_tolerance", f"arms.{arm_id}"),
        f"arms.{arm_id}.useful_novelty_score_tolerance",
    )
    if tolerance > 1.0:
        raise ConfigError(
            f"arms.{arm_id}.useful_novelty_score_tolerance must be <= 1"
        )
    if "decision_precedence" in spec:
        precedence = spec["decision_precedence"]
        if (
            isinstance(precedence, (str, bytes))
            or not isinstance(precedence, Sequence)
            or tuple(precedence) != E2_DECISION_PRECEDENCE
        ):
            raise ConfigError(
                f"arms.{arm_id}.decision_precedence must exactly match "
                f"{list(E2_DECISION_PRECEDENCE)!r}"
            )


def _validate_confirmatory_freeze(config: Mapping[str, Any]) -> None:
    """Reject protocol drift in a configuration labelled frozen-confirmatory."""

    expected_episode = {
        "rounds": 5,
        "candidates_per_round": 4,
        "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "archive_size": 4,
        "max_counterexamples_per_round": 2,
    }
    episode = config["episode"]
    for key, expected in expected_episode.items():
        if episode.get(key) != expected:
            raise ConfigError(
                f"confirmatory config freezes episode.{key}={expected}; "
                f"received {episode.get(key)!r}"
            )

    expected_arms = {
        "L": {"kind": "fixed", "temperature": SCHEDULES["L"][0]},
        "M": {"kind": "fixed", "temperature": SCHEDULES["M"][0]},
        "H": {"kind": "fixed", "temperature": SCHEDULES["H"][0]},
        "A": {"kind": "sequence", "temperatures": list(SCHEDULES["A"])},
        "C": {"kind": "sequence", "temperatures": list(SCHEDULES["C"])},
        "MTX": {"kind": "multi", "temperatures": list(SCHEDULES["MTX"])},
        "E": {
            "kind": "adaptive",
            "initial_temperature": ADAPTIVE_INITIAL,
            "minimum_temperature": LOW_TEMPERATURE,
            "maximum_temperature": HIGH_TEMPERATURE,
            "improvement_step": -ADAPTIVE_DECREASE,
            "stagnation_step": ADAPTIVE_INCREASE,
        },
    }
    if config["arms"] != expected_arms:
        raise ConfigError("confirmatory arms must exactly match all seven frozen treatments")

    worlds = config["worlds"]
    if len(worlds) != 40:
        raise ConfigError("confirmatory config must contain exactly 40 worlds")
    tier_counts = {
        tier: sum(world["depth"] == tier for world in worlds)
        for tier in DEFAULT_DEPTH_TIERS
    }
    if max(tier_counts.values()) - min(tier_counts.values()) > 1:
        raise ConfigError("confirmatory world depths must be approximately equally stratified")
    development_seeds = _load_development_seed_registry()
    overlap = sorted({world["seed"] for world in worlds} & development_seeds)
    if overlap:
        raise ConfigError(f"confirmatory world seeds overlap development registry: {overlap}")

    comparator = config.get("primary_comparator")
    if comparator not in {"M", "A", "C", "MTX"}:
        raise ConfigError("confirmatory primary_comparator must be one of M, A, C, or MTX")
    for key in ("development_config_hash", "development_results_hash"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"confirmatory {key} must be a non-empty frozen hash")

    model = config["model"]
    for key in ("provider", "name", "snapshot"):
        value = model[key]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"confirmatory model.{key} must be a non-empty frozen identifier")
    if model["structured_output"] is not True:
        raise ConfigError("confirmatory model.structured_output must be true")


def _load_development_seed_registry() -> set[int]:
    """Read every used/retired development seed from the frozen registry."""

    try:
        with DEVELOPMENT_SEED_REGISTRY_PATH.open(encoding="utf-8") as handle:
            raw = json.load(handle)
        raw_seeds = raw["seeds"]
        if not isinstance(raw_seeds, list) or not raw_seeds:
            raise TypeError("seeds must be a non-empty array")
        seeds = set(raw_seeds)
        if any(type(seed) is not int for seed in seeds) or len(seeds) != len(raw_seeds):
            raise TypeError("development seeds must be unique integers")
        return seeds
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConfigError(
            f"cannot verify confirmatory/development seed disjointness: {exc}"
        ) from exc


def validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached JSON-native experiment configuration."""

    if not isinstance(config, Mapping):
        raise ConfigError("configuration root must be an object")
    # Round-trip first so custom mappings, tuples, and accidental non-JSON
    # values cannot leak into hashes or summaries.
    normalized = json.loads(_canonical_json(config))

    if _integer(_required(normalized, "schema_version", "config"), "schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    experiment = _required(normalized, "experiment", "config")
    status = _required(normalized, "status", "config")
    if not isinstance(experiment, str) or not experiment.strip():
        raise ConfigError("experiment must be a non-empty string")
    if not isinstance(status, str) or not status.strip():
        raise ConfigError("status must be a non-empty string")

    episode = _mapping(_required(normalized, "episode", "config"), "episode")
    rounds = _integer(_required(episode, "rounds", "episode"), "episode.rounds", minimum=1)
    candidates = _integer(
        _required(episode, "candidates_per_round", "episode"),
        "episode.candidates_per_round",
        minimum=1,
    )
    _integer(
        _required(episode, "max_output_tokens", "episode"),
        "episode.max_output_tokens",
        minimum=1,
    )
    _integer(
        _required(episode, "archive_size", "episode"),
        "episode.archive_size",
        minimum=1,
    )
    counterexample_limit = _integer(
        _required(episode, "max_counterexamples_per_round", "episode"),
        "episode.max_counterexamples_per_round",
        minimum=0,
    )
    if counterexample_limit > 2:
        raise ConfigError("episode.max_counterexamples_per_round cannot exceed runner limit 2")

    worlds = _required(normalized, "worlds", "config")
    if isinstance(worlds, (str, bytes)) or not isinstance(worlds, Sequence) or not worlds:
        raise ConfigError("worlds must be a non-empty array")
    seen_seeds: set[int] = set()
    for index, raw_world in enumerate(worlds):
        world = _mapping(raw_world, f"worlds[{index}]")
        seed = _integer(_required(world, "seed", f"worlds[{index}]"), f"worlds[{index}].seed")
        depth = _integer(
            _required(world, "depth", f"worlds[{index}]"), f"worlds[{index}].depth"
        )
        if seed in seen_seeds:
            raise ConfigError(f"duplicate world seed: {seed}")
        seen_seeds.add(seed)
        if depth not in DEFAULT_DEPTH_TIERS:
            raise ConfigError(f"worlds[{index}].depth must be one of {DEFAULT_DEPTH_TIERS}")

    arms = _mapping(_required(normalized, "arms", "config"), "arms")
    if not arms:
        raise ConfigError("arms cannot be empty")
    unknown = set(arms) - set(ARM_IDS)
    if unknown:
        raise ConfigError(f"unknown arm IDs: {sorted(unknown)}")
    for arm_id, arm_spec in arms.items():
        _validate_arm(
            arm_id,
            arm_spec,
            rounds=rounds,
            candidates_per_round=candidates,
        )

    model = _mapping(_required(normalized, "model", "config"), "model")
    for key in ("provider", "name", "snapshot"):
        value = _required(model, key, "model")
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"model.{key} must be a string or null")
    if type(_required(model, "structured_output", "model")) is not bool:
        raise ConfigError("model.structured_output must be a boolean")
    if status in {"confirmatory-frozen", "frozen-confirmatory"}:
        _validate_confirmatory_freeze(normalized)
    return normalized


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Read and validate a JSON experiment configuration (pilot by default)."""

    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read experiment config {config_path}: {exc}") from exc
    return validate_config(value)


# Descriptive aliases for callers that prefer longer public names.
load_experiment_config = load_config
validate_experiment_config = validate_config


def _policy_from_config(arm_id: str, spec: Mapping[str, Any]) -> Any:
    kind = spec["kind"]
    if kind == "sequence":
        return build_policy(arm_id, schedule=tuple(spec["temperatures"]))
    if kind == "multi":
        return build_policy(arm_id, temperatures=tuple(spec["temperatures"]))
    if kind == "adaptive":
        controller_version = spec.get(
            "controller_version",
            ADAPTIVE_CONTROLLER_E2 if arm_id == "E2" else ADAPTIVE_CONTROLLER_V1,
        )
        e2_kwargs: dict[str, Any] = {}
        if controller_version == ADAPTIVE_CONTROLLER_E2:
            e2_kwargs = {
                "minimum_valid_candidates": int(spec["minimum_valid_candidates"]),
                "minimum_useful_new_behaviors": int(
                    spec["minimum_useful_new_behaviors"]
                ),
                "useful_novelty_score_tolerance": float(
                    spec["useful_novelty_score_tolerance"]
                ),
            }
        return build_policy(
            arm_id,
            controller_version=controller_version,
            initial=float(spec["initial_temperature"]),
            low=float(spec["minimum_temperature"]),
            high=float(spec["maximum_temperature"]),
            decrease=abs(float(spec["improvement_step"])),
            increase=float(spec["stagnation_step"]),
            **e2_kwargs,
        )
    return build_policy(arm_id)


def _arm_execution_order(
    configured_arms: Mapping[str, Any],
    world_index: int,
) -> tuple[str, ...]:
    """Return a cyclic row while preserving the frozen legacy E position."""

    present = set(configured_arms)
    base_order = list(ARM_EXECUTION_BASE_ORDER)
    if "E2" in present:
        e_index = base_order.index("E")
        if "E" in present:
            base_order.insert(e_index + 1, "E2")
        else:
            base_order[e_index] = "E2"
    ordered = tuple(arm_id for arm_id in base_order if arm_id in present)
    if len(ordered) != len(configured_arms):
        raise ConfigError("configured arms cannot be mapped to frozen execution order")
    if not ordered:
        raise ConfigError("configured arms cannot be empty")
    offset = world_index % len(ordered)
    return ordered[offset:] + ordered[:offset]


def _factory_kwargs(context: GeneratorContext) -> dict[str, Any]:
    return context.to_dict()


def _call_generator_factory(factory: GeneratorFactory, context: GeneratorContext) -> Any:
    """Call common factory signatures using only the sanitized context."""

    if not callable(factory):
        raise TypeError("generator_factory must be callable")
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        generator = factory(context)
    else:
        parameters = signature.parameters
        safe_kwargs = _factory_kwargs(context)
        if not parameters:
            generator = factory()
        elif "context" in parameters:
            named = {"context": context}
            if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
                named.update(safe_kwargs)
            generator = factory(**named)
        else:
            accepted = {
                name: safe_kwargs[name]
                for name, item in parameters.items()
                if name in safe_kwargs
                and item.kind
                in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
            }
            has_var_kwargs = any(
                item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()
            )
            if has_var_kwargs:
                accepted.update(safe_kwargs)
            try:
                signature.bind(**accepted)
            except TypeError:
                # A single conventionally named positional parameter (``ctx``
                # for example) receives the same sanitized object.
                try:
                    signature.bind(context)
                except TypeError as exc:
                    raise TypeError(
                        "generator_factory must accept GeneratorContext, no arguments, "
                        "or named public context fields"
                    ) from exc
                generator = factory(context)
            else:
                generator = factory(**accepted)
    if generator is None:
        raise TypeError("generator_factory returned None")
    return generator


def _failure_counts(result: EpisodeResult) -> dict[str, Any]:
    records = [record for round_records in result.rounds for record in round_records]
    syntax = sum(not record.syntax_valid for record in records)
    runtime = sum(record.syntax_valid and not record.runtime_valid for record in records)
    with_errors = sum(record.error is not None for record in records)
    stable_codes = (
        "parse_or_grammar",
        "depth",
        "node_count",
        "output_bound",
        "runtime",
    )
    return {
        "total_candidates": len(records),
        "syntax_failures": syntax,
        "runtime_failures": runtime,
        "invalid_candidates": sum(
            not (record.syntax_valid and record.runtime_valid) for record in records
        ),
        "records_with_errors": with_errors,
        "by_code": {
            code: sum(code in record.failure_codes for record in records)
            for code in stable_codes
        },
    }


def _round_best_scores(result: EpisodeResult) -> list[float]:
    scores: list[float] = []
    for records in result.rounds:
        valid = [
            record.probe_score
            for record in records
            if record.syntax_valid and record.runtime_valid
        ]
        scores.append(float(max(valid, default=0.0)))
    return scores


def _usage_budget(result: EpisodeResult) -> dict[str, Any]:
    records = [record for items in result.rounds for record in items]
    available = bool(records) and all(
        record.actual_usage_available for record in records
    )
    if not available:
        return {
            "actual_usage_available": False,
            "actual_input_tokens": None,
            "actual_output_tokens": None,
            "actual_billed_tokens": None,
            "provider_requests": None,
            "retry_count": None,
            "latency_ms_total": None,
            "latency_ms_mean": None,
            "seed_supported_for_all_calls": None,
            "prompt_cache_hit_tokens": None,
            "prompt_cache_miss_tokens": None,
            "reasoning_tokens": None,
        }
    provider_requests = sum(int(record.provider_request_count) for record in records)
    latency_total = sum(float(record.latency_ms) for record in records)
    input_tokens = sum(int(record.input_tokens) for record in records)
    output_tokens = sum(int(record.output_tokens) for record in records)
    seed_flags = [record.seed_supported for record in records]
    seed_supported = (
        all(flag is True for flag in seed_flags)
        if all(flag is not None for flag in seed_flags)
        else None
    )
    optional_usage: dict[str, int | None] = {}
    for field in (
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
    ):
        values = [getattr(record, field) for record in records]
        optional_usage[field] = (
            sum(int(value) for value in values)
            if all(value is not None for value in values)
            else None
        )
    return {
        "actual_usage_available": True,
        "actual_input_tokens": input_tokens,
        "actual_output_tokens": output_tokens,
        "actual_billed_tokens": input_tokens + output_tokens,
        "provider_requests": provider_requests,
        "retry_count": provider_requests - len(records),
        "latency_ms_total": latency_total,
        "latency_ms_mean": latency_total / len(records),
        "seed_supported_for_all_calls": seed_supported,
        **optional_usage,
    }


def _candidate_expression(candidate: Any) -> str:
    try:
        from .dsl import to_sexpr

        return to_sexpr(candidate)
    except Exception:
        return str(candidate)


_E2_CONTROLLER_TRACE_FIELDS = (
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
)


def _sanitized_controller_trace(result: EpisodeResult) -> list[dict[str, Any]]:
    """Whitelist the scalar E2 trace without serializing arbitrary policy state."""

    trace: list[dict[str, Any]] = []
    for raw in result.policy_history:
        if raw.get("controller_version") != ADAPTIVE_CONTROLLER_E2:
            continue
        missing = [field for field in _E2_CONTROLLER_TRACE_FIELDS if field not in raw]
        if missing:
            raise ValueError(f"E2 controller trace is missing required fields: {missing}")
        record: dict[str, Any] = {}
        for field in _E2_CONTROLLER_TRACE_FIELDS:
            value = raw[field]
            if type(value) not in {str, int, float, bool}:
                raise TypeError(f"E2 controller trace field {field} must be scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"E2 controller trace field {field} must be finite")
            record[field] = value
        trace.append(record)
    return trace


def _run_summary(
    *,
    context: GeneratorContext,
    result: EpisodeResult,
    run_id: str,
    arm_id: str,
    arm_hash: str,
    world_index: int,
    world_seed: int,
    world_depth: int,
    world_hash: str,
    sampling_base_seed: int,
    probe_size: int,
    test_size: int,
    max_counterexamples_per_round: int,
) -> dict[str, Any]:
    candidate_count = result.candidate_count
    final_probe = None
    if result.final_candidate is not None:
        final_probe = float(result.final_candidate.probe_score)
    final_test = None if result.final_test_score is None else float(result.final_test_score)
    final_candidate = result.final_candidate
    rounds = int(context.episode["rounds"])
    candidates_per_round = int(context.episode["candidates_per_round"])
    planned_calls = rounds * candidates_per_round
    usage_budget = _usage_budget(result)
    summary = {
        "run_id": run_id,
        "arm_id": arm_id,
        "arm_hash": arm_hash,
        "world": {
            "index": world_index,
            "seed": world_seed,
            "depth": world_depth,
            "world_hash": world_hash,
        },
        "world_hash": world_hash,
        "sampling_base_seed": sampling_base_seed,
        "budget": {
            "rounds": rounds,
            "candidates_per_round": candidates_per_round,
            "generation_calls_planned": planned_calls,
            "generation_calls_completed": candidate_count,
            "max_output_tokens_per_call": context.max_output_tokens,
            "max_output_tokens_planned": planned_calls * context.max_output_tokens,
            "max_output_tokens_completed_ceiling": candidate_count
            * context.max_output_tokens,
            **usage_budget,
            "probe_points_per_candidate": probe_size,
            "probe_point_evaluations_planned": planned_calls * probe_size,
            "feedback_rounds": len(result.rounds),
            "counterexamples_released": len(result.counterexamples),
            "counterexamples_release_limit": rounds * max_counterexamples_per_round,
            "final_test_points_planned": test_size,
            "final_test_points_evaluated": test_size if result.final_test is not None else 0,
        },
        "probe": {
            "round_best_scores": _round_best_scores(result),
            "final_selected_score": final_probe,
            "final_selected_accuracy": final_probe,
            "selected_candidate_canonical_hash": (
                None if final_candidate is None else final_candidate.canonical_hash
            ),
            "selected_candidate_behavior_hash": (
                None if final_candidate is None else final_candidate.behavior_hash
            ),
        },
        "final_test": {
            "evaluated": result.final_test is not None,
            "score": final_test,
            "accuracy": final_test,
            "world_solved": final_test == 1.0 if final_test is not None else False,
        },
        "temperature_trajectory": [float(value) for value in result.temperatures],
        "slot_temperature_trajectory": [
            [float(value) for value in row] for row in result.slot_temperatures
        ],
        "candidates": [
            {
                "round_index": record.round_index,
                "candidate_index": record.candidate_index,
                "temperature": float(record.temperature),
                "probe_score": float(record.probe_score),
                "syntax_valid": bool(record.syntax_valid),
                "runtime_valid": bool(record.runtime_valid),
                "failure_codes": list(record.failure_codes),
                "input_tokens": record.input_tokens,
                "output_tokens": record.output_tokens,
                "latency_ms": record.latency_ms,
                "provider_request_count": record.provider_request_count,
                "seed_supported": record.seed_supported,
                "provider_model": record.provider_model,
                "finish_reason": record.finish_reason,
                "prompt_cache_hit_tokens": record.prompt_cache_hit_tokens,
                "prompt_cache_miss_tokens": record.prompt_cache_miss_tokens,
                "reasoning_tokens": record.reasoning_tokens,
                "candidate_format": record.candidate_format,
                "provider_fingerprint": record.provider_fingerprint,
                # Prompts are reproducible from the frozen protocol but may
                # contain long provider-facing context. Persist only a digest;
                # the raw prompt never enters a durable result artifact.
                "prompt_sha256": _hash(record.prompt),
                # A syntax-invalid candidate may contain arbitrary assistant
                # text. Keep only a closed sentinel for that case. Parsed ASTs
                # are safe to render back into the frozen DSL.
                "candidate_expression": (
                    _candidate_expression(record.candidate)
                    if record.syntax_valid
                    else "__INVALID_CANDIDATE_EXPRESSION__"
                ),
                "node_count": int(record.node_count),
                "canonical_hash": str(record.canonical_hash),
                "behavior_hash": str(record.behavior_hash),
            }
            for records in result.rounds
            for record in records
        ],
        "failure_counts": _failure_counts(result),
    }
    controller_trace = _sanitized_controller_trace(result)
    if controller_trace:
        # E1 and all historical configs retain their byte-for-byte summary
        # shape.  Only an explicitly configured E2 run receives this field.
        summary["controller_trace"] = controller_trace
    return summary


def _token_fairness(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    available = bool(runs) and all(
        run["budget"]["actual_usage_available"] for run in runs
    )
    if not available:
        return {
            "available": False,
            "threshold": 0.02,
            "passed": False,
            "relative_range": None,
            "mean_billed_tokens_per_call_by_arm": {},
        }
    by_arm: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        by_arm.setdefault(str(run["arm_id"]), []).append(run)
    means: dict[str, float] = {}
    for arm_id, arm_runs in by_arm.items():
        tokens = sum(int(run["budget"]["actual_billed_tokens"]) for run in arm_runs)
        calls = sum(int(run["budget"]["generation_calls_completed"]) for run in arm_runs)
        means[arm_id] = tokens / calls if calls else 0.0
    values = list(means.values())
    if not values or min(values) == 0:
        relative_range = 0.0 if values and max(values) == 0 else None
    else:
        relative_range = (max(values) - min(values)) / min(values)
    passed = relative_range is not None and relative_range <= 0.02
    return {
        "available": True,
        "threshold": 0.02,
        "passed": passed,
        "relative_range": relative_range,
        "mean_billed_tokens_per_call_by_arm": means,
    }


def _aggregate_budget(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    keys = (
        "generation_calls_planned",
        "generation_calls_completed",
        "max_output_tokens_planned",
        "max_output_tokens_completed_ceiling",
        "probe_point_evaluations_planned",
        "feedback_rounds",
        "counterexamples_released",
        "counterexamples_release_limit",
        "final_test_points_planned",
        "final_test_points_evaluated",
    )
    totals = {
        key: sum(int(run["budget"][key]) for run in runs)
        for key in keys
    }
    totals["run_count"] = len(runs)
    usage_available = bool(runs) and all(
        run["budget"]["actual_usage_available"] for run in runs
    )
    totals["actual_usage_available"] = usage_available
    usage_keys = (
        "actual_input_tokens",
        "actual_output_tokens",
        "actual_billed_tokens",
        "provider_requests",
        "retry_count",
        "latency_ms_total",
    )
    for key in usage_keys:
        totals[key] = (
            sum(run["budget"][key] for run in runs)
            if usage_available
            else None
        )
    for key in (
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
    ):
        values = [run["budget"].get(key) for run in runs]
        totals[key] = (
            sum(int(value) for value in values)
            if usage_available and all(value is not None for value in values)
            else None
        )
    totals["token_fairness"] = _token_fairness(runs)
    return totals


def _evidence_metadata(
    generator_factory: GeneratorFactory,
    config: Mapping[str, Any],
    *,
    actual_usage_available: bool,
    token_fairness_passed: bool,
    observed_models: Sequence[str],
    finish_reason_counts: Mapping[str, int],
    expected_completion_count: int,
    reasoning_tokens: int | None,
) -> tuple[bool, str, str]:
    """Return a conservative evidence flag, scope, and explanatory reason."""

    declared = getattr(generator_factory, "evidence", False) is True
    model = config["model"]
    missing_identity = [
        key
        for key in ("provider", "name", "snapshot")
        if not isinstance(model.get(key), str) or not str(model[key]).strip()
    ]
    expected_snapshot = model.get("snapshot")
    model_observation_matches = (
        isinstance(expected_snapshot, str)
        and bool(expected_snapshot.strip())
        and set(observed_models) == {expected_snapshot}
    )
    clean_finishes = (
        set(finish_reason_counts) == {"stop"}
        and sum(finish_reason_counts.values()) == expected_completion_count
    )
    reasoning_mode_clean = reasoning_tokens in {None, 0}
    evidence = (
        declared
        and not missing_identity
        and model_observation_matches
        and clean_finishes
        and reasoning_mode_clean
        and actual_usage_available
        and token_fairness_passed
    )
    if not evidence:
        scope = "non-evidence"
    elif config["status"] == "development-only":
        scope = "development"
    elif config["status"] in {"confirmatory-frozen", "frozen-confirmatory"}:
        scope = "confirmatory"
    else:
        # Merely using a word such as "confirmatory" in an arbitrary status
        # must not upgrade an unfrozen artifact.
        scope = "exploratory"

    factory_reason = getattr(generator_factory, "evidence_reason", None)
    if not declared:
        reason = str(
            factory_reason
            or "generator factory did not explicitly declare evidence=True"
        )
    elif missing_identity:
        reason = "model identity is incomplete: " + ", ".join(missing_identity)
    elif not actual_usage_available:
        reason = "actual token usage is unavailable; matched-budget fairness is not auditable"
    elif not model_observation_matches:
        reason = (
            "response-echoed model identity does not exactly match the configured "
            f"snapshot: expected={expected_snapshot!r}, observed={list(observed_models)!r}"
        )
    elif not clean_finishes:
        reason = (
            "provider finish reasons are incomplete or contain non-stop outcomes: "
            f"{dict(finish_reason_counts)!r}"
        )
    elif not reasoning_mode_clean:
        reason = (
            "provider reported nonzero reasoning tokens despite the frozen "
            f"thinking-disabled treatment: {reasoning_tokens}"
        )
    elif not token_fairness_passed:
        reason = "actual billed-token difference exceeds the frozen 2% fairness threshold"
    else:
        reason = f"factory explicitly declared evidence; scope={scope}"
    return evidence, scope, reason


def run_experiment(
    config: Mapping[str, Any] | str | Path = DEFAULT_CONFIG_PATH,
    generator_factory: GeneratorFactory | None = None,
) -> dict[str, Any]:
    """Run every configured world/arm pair and return a JSON-safe summary.

    The factory is called once per pair.  It receives only public metadata in
    :class:`GeneratorContext`; the generated world remains inside this
    orchestrator and :func:`run_episode`.
    """

    if generator_factory is None:
        raise TypeError("generator_factory is required")
    validated = load_config(config) if isinstance(config, (str, Path)) else validate_config(config)
    config_hash = _hash(validated)
    episode = validated["episode"]
    max_output_tokens = int(episode["max_output_tokens"])
    rounds = int(episode["rounds"])
    candidates_per_round = int(episode["candidates_per_round"])
    archive_size = int(episode["archive_size"])
    max_counterexamples_per_round = int(episode["max_counterexamples_per_round"])

    arm_hashes = {
        arm_id: _hash({"arm_id": arm_id, "spec": arm_spec})
        for arm_id, arm_spec in validated["arms"].items()
    }
    arm_summaries = [
        {"arm_id": arm_id, "arm_hash": arm_hashes[arm_id]}
        for arm_id in validated["arms"]
    ]
    world_summaries: list[dict[str, Any]] = []
    run_summaries: list[dict[str, Any]] = []
    pending_runs: list[tuple[EpisodeResult, Any, dict[str, Any]]] = []

    for world_index, world_spec in enumerate(validated["worlds"]):
        seed = int(world_spec["seed"])
        depth = int(world_spec["depth"])
        world = generate_world(seed, depth=depth)
        world_hash = str(
            getattr(world, "world_hash", _hash({"seed": seed, "depth": depth}))
        )
        arm_execution_order = _arm_execution_order(validated["arms"], world_index)
        world_summaries.append(
            {
                "index": world_index,
                "seed": seed,
                "depth": depth,
                "world_hash": world_hash,
                "arm_execution_order": list(arm_execution_order),
            }
        )
        probe_size = len(tuple(getattr(world, "probe", ())))
        test_size = len(tuple(getattr(world, "test", ())))

        for arm_id in arm_execution_order:
            arm_spec = validated["arms"][arm_id]
            arm_hash = arm_hashes[arm_id]
            run_id = _hash(
                {
                    "config_hash": config_hash,
                    "world_hash": world_hash,
                    "arm_hash": arm_hash,
                }
            )
            context = GeneratorContext(
                experiment=validated["experiment"],
                episode=MappingProxyType(dict(episode)),
                model=MappingProxyType(dict(validated["model"])),
                max_output_tokens=max_output_tokens,
            )
            generator = _call_generator_factory(generator_factory, context)
            policy = _policy_from_config(arm_id, arm_spec)
            verifier = Verifier(counterexample_limit=max_counterexamples_per_round)
            # Common random numbers: every world/arm pair receives the same
            # config-independent per-round/per-slot call seeds. Neither world,
            # arm, nor config identity may become a side channel alongside the
            # temperature treatment.
            sampling_seed = SAMPLING_BASE_SEED
            result = run_episode(
                world,
                generator,
                verifier=verifier,
                policy=policy,
                rounds=rounds,
                candidates_per_round=candidates_per_round,
                archive_capacity=archive_size,
                max_counterexamples=rounds * max_counterexamples_per_round,
                max_output_tokens=max_output_tokens,
                max_counterexamples_per_round=max_counterexamples_per_round,
                seed=sampling_seed,
                evaluate_test=False,
            )
            pending_runs.append(
                (
                    result,
                    verifier,
                    {
                        "context": context,
                        "run_id": run_id,
                        "arm_id": arm_id,
                        "arm_hash": arm_hash,
                        "world_index": world_index,
                        "world_seed": seed,
                        "world_depth": depth,
                        "world_hash": world_hash,
                        "sampling_base_seed": sampling_seed,
                        "probe_size": probe_size,
                        "test_size": test_size,
                        "max_counterexamples_per_round": max_counterexamples_per_round,
                    },
                )
            )

    # Private-test evaluation is globally delayed until every configured model
    # call has completed. No test-derived value can therefore influence a later
    # arm/world generation, provider progress hook, or failure decision.
    for result, verifier, summary_kwargs in pending_runs:
        result.final_test = evaluate_episode_test(result, verifier=verifier)
        run_summaries.append(_run_summary(result=result, **summary_kwargs))

    aggregate_budget = _aggregate_budget(run_summaries)
    token_fairness = aggregate_budget["token_fairness"]
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
    finish_reason_counts: dict[str, int] = {}
    for run in run_summaries:
        for candidate in run["candidates"]:
            reason = candidate.get("finish_reason")
            if reason:
                finish_reason_counts[str(reason)] = (
                    finish_reason_counts.get(str(reason), 0) + 1
                )
    factory_evidence, evidence_scope, evidence_reason = _evidence_metadata(
        generator_factory,
        validated,
        actual_usage_available=bool(aggregate_budget["actual_usage_available"]),
        token_fairness_passed=bool(token_fairness["passed"]),
        observed_models=observed_models,
        finish_reason_counts=finish_reason_counts,
        expected_completion_count=int(aggregate_budget["generation_calls_planned"]),
        reasoning_tokens=aggregate_budget.get("reasoning_tokens"),
    )
    mode = str(getattr(generator_factory, "mode", "injected-generator"))
    summary: dict[str, Any] = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "experiment": validated["experiment"],
        "config_status": validated["status"],
        "config_hash": config_hash,
        "evidence": factory_evidence,
        "evidence_scope": evidence_scope,
        "mode": mode,
        "model": {
            "configured": dict(validated["model"]),
            "observed_response_models": observed_models,
            "observed_system_fingerprints": observed_fingerprints,
            "finish_reason_counts": finish_reason_counts,
        },
        "arms": arm_summaries,
        "arm_hashes": arm_hashes,
        "worlds": world_summaries,
        "world_hashes": [item["world_hash"] for item in world_summaries],
        "runs": run_summaries,
        "budget": aggregate_budget,
    }
    if not factory_evidence:
        summary["evidence_reason"] = evidence_reason
    # Fail here, close to the source, rather than returning an artifact that
    # only breaks when a caller tries to persist it.
    _canonical_json(summary)
    return summary


def run_pilot(generator_factory: GeneratorFactory) -> dict[str, Any]:
    """Run the validated development pilot config with an injected factory."""

    return run_experiment(DEFAULT_CONFIG_PATH, generator_factory)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the explicitly non-evidential offline smoke path from the command line."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--offline-smoke",
        action="store_true",
        help="use a fixed offline generator; output is always evidence=false",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    if not args.offline_smoke:
        parser.error("no provider factory is bundled; pass --offline-smoke for plumbing only")
    summary = run_experiment(args.config, OfflineSmokeGeneratorFactory())
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


__all__ = [
    "ConfigError",
    "ARM_EXECUTION_BASE_ORDER",
    "DEFAULT_CONFIG_PATH",
    "DEVELOPMENT_SEED_REGISTRY_PATH",
    "GeneratorContext",
    "GeneratorFactory",
    "OfflineSmokeGeneratorFactory",
    "SAMPLING_BASE_SEED",
    "SUMMARY_SCHEMA_VERSION",
    "load_config",
    "load_experiment_config",
    "main",
    "run_experiment",
    "run_pilot",
    "validate_config",
    "validate_experiment_config",
]


if __name__ == "__main__":
    raise SystemExit(main())
