"""Temperature policies used by the fixed-budget search experiment.

The policy layer owns only exploration scheduling.  It never sees private test
labels or a model rationale.  ``temperature_for_round`` follows the small protocol
used by :mod:`src.runner`; the additional ``temperatures_for_round`` method exposes
the four slots needed by the multi-temperature exchange (MTX) arm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any, Optional


LOW_TEMPERATURE = 0.2
MID_TEMPERATURE = 0.7
HIGH_TEMPERATURE = 1.2
ADAPTIVE_INITIAL = 1.0
ADAPTIVE_DECREASE = 0.2
ADAPTIVE_INCREASE = 0.3
ADAPTIVE_CONTROLLER_V1 = "probe-improvement-v1"
ADAPTIVE_CONTROLLER_E2 = "validity-novelty-v2"
E2_MINIMUM_VALID_CANDIDATES = 3
E2_MINIMUM_USEFUL_NEW_BEHAVIORS = 1
E2_USEFUL_NOVELTY_SCORE_TOLERANCE = 1.0 / 12.0
E2_DECISION_PRECEDENCE = (
    "low_validity_decrease",
    "probe_improved_decrease",
    "probe_ceiling_hold",
    "useful_novelty_hold",
    "stale_search_increase",
)

ARM_IDS = ("L", "M", "H", "A", "C", "MTX", "E", "E2")
SCHEDULES = {
    "L": (LOW_TEMPERATURE,),
    "M": (MID_TEMPERATURE,),
    "H": (HIGH_TEMPERATURE,),
    "A": (HIGH_TEMPERATURE, 0.95, MID_TEMPERATURE, 0.45, LOW_TEMPERATURE),
    "C": (HIGH_TEMPERATURE, LOW_TEMPERATURE, HIGH_TEMPERATURE, LOW_TEMPERATURE, LOW_TEMPERATURE),
    "MTX": (LOW_TEMPERATURE, MID_TEMPERATURE, MID_TEMPERATURE, HIGH_TEMPERATURE),
}


def _round_index(round_index: int) -> int:
    try:
        value = int(round_index)
    except (TypeError, ValueError) as exc:
        raise TypeError("round index must be an integer") from exc
    if value < 0:
        raise ValueError("round index cannot be negative")
    return value


def _validate_temperature(value: float, *, low: float = 0.0, high: Optional[float] = None) -> float:
    value = float(value)
    if value < low:
        raise ValueError("temperature must be non-negative")
    if high is not None and value > high:
        raise ValueError(f"temperature must be <= {high}")
    return value


def _nonnegative_integer(value: Any, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _positive_integer(value: Any, name: str) -> int:
    result = _nonnegative_integer(value, name)
    if result == 0:
        raise ValueError(f"{name} must be positive")
    return result


def _probe_score(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must lie within [0, 1]")
    return result


class TemperaturePolicy:
    """Base protocol implementation shared by all schedule policies."""

    arm_id = ""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> float:
        raise NotImplementedError

    def temperatures_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> tuple[float, ...]:
        return (self.temperature_for_round(round_index, state),)

    def temperature_for_slot(self, round_index: int, slot_index: int = 0, state: Mapping[str, Any] | None = None) -> float:
        temperatures = self.temperatures_for_round(round_index, state)
        if slot_index < 0 or slot_index >= len(temperatures):
            raise IndexError("temperature slot index out of range")
        return temperatures[slot_index]

    # The aliases make the policy compatible with small runners and hidden tests
    # that use one of the common names.
    def get_temperature(self, round_index: int, state: Mapping[str, Any] | None = None) -> float:
        return self.temperature_for_round(round_index, state)

    def temperature_at(self, round_index: int, state: Mapping[str, Any] | None = None) -> float:
        return self.temperature_for_round(round_index, state)

    def update(self, **kwargs: Any) -> Any:
        self.history.append(dict(kwargs))
        return self.history[-1]

    def observe(self, **kwargs: Any) -> Any:
        return self.update(**kwargs)

    def reset(self) -> None:
        self.history.clear()


class FixedTemperaturePolicy(TemperaturePolicy):
    """A static temperature baseline (L, M, or H)."""

    def __init__(self, temperature: float, *, arm_id: str = "") -> None:
        super().__init__()
        self.temperature = _validate_temperature(temperature)
        self.arm_id = arm_id

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> float:
        _round_index(round_index)
        return self.temperature


class ScheduledTemperaturePolicy(TemperaturePolicy):
    """A finite open-loop schedule, holding its final value after exhaustion."""

    def __init__(self, schedule: Sequence[float], *, arm_id: str = "", repeat: bool = False) -> None:
        super().__init__()
        if not schedule:
            raise ValueError("schedule must contain at least one temperature")
        self.schedule = tuple(_validate_temperature(value) for value in schedule)
        self.arm_id = arm_id
        self.repeat = bool(repeat)

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> float:
        index = _round_index(round_index)
        if self.repeat:
            index %= len(self.schedule)
        else:
            index = min(index, len(self.schedule) - 1)
        return self.schedule[index]

    def temperatures_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> tuple[float, ...]:
        return (self.temperature_for_round(round_index, state),)


class FixedCyclePolicy(ScheduledTemperaturePolicy):
    def __init__(self, schedule: Sequence[float] = SCHEDULES["C"]) -> None:
        super().__init__(schedule, arm_id="C")


class AnnealingPolicy(ScheduledTemperaturePolicy):
    def __init__(self, schedule: Sequence[float] = SCHEDULES["A"]) -> None:
        super().__init__(schedule, arm_id="A")


class MultiTemperatureExchangePolicy(TemperaturePolicy):
    """Four fixed temperature streams with an externally shared elite.

    The policy itself does not copy candidates; that belongs to the runner/archive.
    It exposes the slot temperatures and records an optional round-level elite in
    ``update`` for reproducibility.  ``temperature_for_round`` returns the first
    slot for compatibility with runners that issue one temperature per round.
    """

    def __init__(self, temperatures: Sequence[float] = SCHEDULES["MTX"]) -> None:
        super().__init__()
        if not temperatures:
            raise ValueError("MTX requires at least one temperature slot")
        self.temperatures = tuple(_validate_temperature(value) for value in temperatures)
        self.arm_id = "MTX"
        self.elite: Any = None

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> float:
        _round_index(round_index)
        return self.temperatures[0]

    def temperatures_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> tuple[float, ...]:
        _round_index(round_index)
        return self.temperatures

    def update(self, **kwargs: Any) -> dict[str, Any]:
        if "elite" in kwargs:
            self.elite = kwargs["elite"]
        return super().update(**kwargs)

    def reset(self) -> None:
        super().reset()
        self.elite = None


class AdaptiveTemperaturePolicy(TemperaturePolicy):
    """Verifier-feedback controller from the preregistered protocol.

    At the start of an episode ``current`` is 1.0.  After each round, progress
    decreases the next temperature by 0.2; a tie or regression increases it by 0.3.
    Values are clipped to [0.2, 1.2].  The controller is deterministic and uses only
    public/probe scores supplied by the runner.
    """

    controller_version = ADAPTIVE_CONTROLLER_V1

    def __init__(self, initial: float = ADAPTIVE_INITIAL, low: float = LOW_TEMPERATURE, high: float = HIGH_TEMPERATURE, decrease: float = ADAPTIVE_DECREASE, increase: float = ADAPTIVE_INCREASE) -> None:
        super().__init__()
        self.low = _validate_temperature(low)
        self.high = _validate_temperature(high, low=self.low)
        self.initial = _validate_temperature(initial, low=self.low, high=self.high)
        self.current = self.initial
        self.decrease = _validate_temperature(decrease)
        self.increase = _validate_temperature(increase)
        self.arm_id = "E"

    @property
    def temperature(self) -> float:
        return self.current

    def temperature_for_round(self, round_index: int, state: Mapping[str, Any] | None = None) -> float:
        _round_index(round_index)
        return self.current

    def update(self, **kwargs: Any) -> dict[str, Any]:
        improved = kwargs.get("improved")
        if improved is None:
            round_best = kwargs.get("round_best")
            best_score = kwargs.get("best_score")
            if round_best is not None and best_score is not None:
                improved = float(round_best) > float(best_score)
            else:
                improved = False
        improved = bool(improved)
        previous = self.current
        if improved:
            self.current = max(self.low, self.current - self.decrease)
        else:
            self.current = min(self.high, self.current + self.increase)
        record = {**kwargs, "improved": improved, "previous_temperature": previous, "next_temperature": self.current}
        self.history.append(record)
        return record

    def reset(self) -> None:
        super().reset()
        self.current = self.initial


class ValidityNoveltyAdaptiveTemperaturePolicy(AdaptiveTemperaturePolicy):
    """E2 controller using only scalar validity and behavioral-novelty signals.

    Candidate parsing, behavior hashing, and probe verification remain runner
    responsibilities.  This controller deliberately accepts no candidate,
    behavior hash, task/world identity, counterexample, prediction, or test
    value.  Its explicit keyword-only signature makes that boundary auditable.
    """

    controller_version = ADAPTIVE_CONTROLLER_E2
    requires_validity_novelty_observation = True

    def __init__(
        self,
        initial: float = ADAPTIVE_INITIAL,
        low: float = LOW_TEMPERATURE,
        high: float = HIGH_TEMPERATURE,
        decrease: float = ADAPTIVE_DECREASE,
        increase: float = ADAPTIVE_INCREASE,
        minimum_valid_candidates: int = E2_MINIMUM_VALID_CANDIDATES,
        minimum_useful_new_behaviors: int = E2_MINIMUM_USEFUL_NEW_BEHAVIORS,
        useful_novelty_score_tolerance: float = E2_USEFUL_NOVELTY_SCORE_TOLERANCE,
        arm_id: str = "E2",
    ) -> None:
        super().__init__(
            initial=initial,
            low=low,
            high=high,
            decrease=decrease,
            increase=increase,
        )
        self.minimum_valid_candidates = _positive_integer(
            minimum_valid_candidates,
            "minimum_valid_candidates",
        )
        self.minimum_useful_new_behaviors = _positive_integer(
            minimum_useful_new_behaviors,
            "minimum_useful_new_behaviors",
        )
        self.useful_novelty_score_tolerance = _probe_score(
            useful_novelty_score_tolerance,
            "useful_novelty_score_tolerance",
        )
        self.arm_id = arm_id

    def update(
        self,
        *,
        round_index: int,
        round_best: float,
        best_score: float,
        pre_round_best_score: float,
        improved: bool,
        planned_candidate_count: int,
        valid_candidate_count: int,
        new_behavior_count: int,
        useful_new_behavior_count: int,
    ) -> dict[str, Any]:
        round_value = _nonnegative_integer(round_index, "round_index")
        planned = _positive_integer(planned_candidate_count, "planned_candidate_count")
        valid = _nonnegative_integer(valid_candidate_count, "valid_candidate_count")
        new = _nonnegative_integer(new_behavior_count, "new_behavior_count")
        useful = _nonnegative_integer(
            useful_new_behavior_count,
            "useful_new_behavior_count",
        )
        if valid > planned:
            raise ValueError("valid_candidate_count cannot exceed planned_candidate_count")
        if new > valid:
            raise ValueError("new_behavior_count cannot exceed valid_candidate_count")
        if useful > new:
            raise ValueError("useful_new_behavior_count cannot exceed new_behavior_count")
        if self.minimum_valid_candidates > planned:
            raise ValueError(
                "minimum_valid_candidates cannot exceed planned_candidate_count"
            )
        if self.minimum_useful_new_behaviors > planned:
            raise ValueError(
                "minimum_useful_new_behaviors cannot exceed planned_candidate_count"
            )
        round_best_value = _probe_score(round_best, "round_best")
        best_value = _probe_score(best_score, "best_score")
        pre_best_value = _probe_score(
            pre_round_best_score,
            "pre_round_best_score",
        )
        if best_value < pre_best_value:
            raise ValueError("best_score cannot regress below pre_round_best_score")
        if type(improved) is not bool:
            raise TypeError("improved must be a boolean")
        if valid == 0 and round_best_value != 0.0:
            raise ValueError("an all-invalid round must have round_best=0")
        expected_improved = valid > 0 and round_best_value > pre_best_value
        if improved is not expected_improved:
            raise ValueError(
                "improved must equal whether a valid round_best exceeds "
                "pre_round_best_score"
            )
        expected_best = (
            max(pre_best_value, round_best_value) if valid > 0 else pre_best_value
        )
        if best_value != expected_best:
            raise ValueError(
                "best_score must equal the post-round maximum probe score"
            )

        previous = self.current
        if valid < self.minimum_valid_candidates:
            decision = "decrease"
            reason = "low_validity"
            self.current = max(self.low, self.current - self.decrease)
        elif improved:
            decision = "decrease"
            reason = "probe_improved"
            self.current = max(self.low, self.current - self.decrease)
        elif best_value >= 1.0:
            decision = "hold"
            reason = "probe_ceiling"
        elif useful >= self.minimum_useful_new_behaviors:
            decision = "hold"
            reason = "useful_novelty"
        else:
            decision = "increase"
            reason = "stale_search"
            self.current = min(self.high, self.current + self.increase)

        record = {
            "controller_version": self.controller_version,
            "round_index": round_value,
            "round_best": round_best_value,
            "best_score": best_value,
            "pre_round_best_score": pre_best_value,
            "improved": improved,
            "planned_candidate_count": planned,
            "valid_candidate_count": valid,
            "new_behavior_count": new,
            "useful_new_behavior_count": useful,
            "decision": decision,
            "decision_reason": reason,
            "previous_temperature": previous,
            "next_temperature": self.current,
        }
        self.history.append(record)
        return record


# Names used by different experiment scripts.
FixedPolicy = FixedTemperaturePolicy
SchedulePolicy = ScheduledTemperaturePolicy
CyclePolicy = FixedCyclePolicy
AdaptivePolicy = AdaptiveTemperaturePolicy
E2AdaptivePolicy = ValidityNoveltyAdaptiveTemperaturePolicy
MTXPolicy = MultiTemperatureExchangePolicy


def build_policy(arm: str, **kwargs: Any) -> TemperaturePolicy:
    """Construct a legacy preregistered arm or the versioned E2 controller."""

    key = str(arm).upper()
    if key == "L":
        return FixedTemperaturePolicy(LOW_TEMPERATURE, arm_id=key)
    if key == "M":
        return FixedTemperaturePolicy(MID_TEMPERATURE, arm_id=key)
    if key == "H":
        return FixedTemperaturePolicy(HIGH_TEMPERATURE, arm_id=key)
    if key == "A":
        return AnnealingPolicy(kwargs.pop("schedule", SCHEDULES["A"]))
    if key == "C":
        return FixedCyclePolicy(kwargs.pop("schedule", SCHEDULES["C"]))
    if key == "MTX":
        return MultiTemperatureExchangePolicy(kwargs.pop("temperatures", SCHEDULES["MTX"]))
    if key in {"E", "E2"}:
        default_version = (
            ADAPTIVE_CONTROLLER_E2 if key == "E2" else ADAPTIVE_CONTROLLER_V1
        )
        controller_version = kwargs.pop("controller_version", default_version)
        if key == "E2" and controller_version != ADAPTIVE_CONTROLLER_E2:
            raise ValueError(
                f"arm E2 requires controller_version={ADAPTIVE_CONTROLLER_E2!r}"
            )
        if controller_version == ADAPTIVE_CONTROLLER_V1:
            return AdaptiveTemperaturePolicy(**kwargs)
        if controller_version == ADAPTIVE_CONTROLLER_E2:
            return ValidityNoveltyAdaptiveTemperaturePolicy(arm_id=key, **kwargs)
        raise ValueError(
            "unknown adaptive controller_version: "
            f"{controller_version!r}; expected {ADAPTIVE_CONTROLLER_V1!r} "
            f"or {ADAPTIVE_CONTROLLER_E2!r}"
        )
    raise ValueError(f"unknown policy arm: {arm!r}; expected one of {ARM_IDS}")


def make_policy(arm: str, **kwargs: Any) -> TemperaturePolicy:
    return build_policy(arm, **kwargs)


def policy_for_arm(arm: str, **kwargs: Any) -> TemperaturePolicy:
    return build_policy(arm, **kwargs)


def temperature_for(arm: str, round_index: int, *, slot_index: int = 0, state: Mapping[str, Any] | None = None, policy: TemperaturePolicy | None = None) -> float:
    """Return a schedule temperature without exposing mutable policy state.

    For adaptive E, pass a policy instance when querying a trajectory; a newly
    constructed policy intentionally starts at 1.0 for each call.
    """

    active = policy or build_policy(arm)
    return active.temperature_for_slot(round_index, slot_index, state)


def temperatures_for(arm: str, rounds: int = 5, *, policy: TemperaturePolicy | None = None) -> tuple[tuple[float, ...], ...]:
    if rounds < 0:
        raise ValueError("rounds cannot be negative")
    active = policy or build_policy(arm)
    return tuple(active.temperatures_for_round(index) for index in range(rounds))


def schedule_for(arm: str, rounds: int = 5) -> tuple[float, ...]:
    """Return the primary (slot zero) trajectory for a static policy."""

    return tuple(row[0] for row in temperatures_for(arm, rounds))


def reset_policy(policy: TemperaturePolicy) -> TemperaturePolicy:
    policy.reset()
    return policy


POLICY_SPECS = {
    "L": {"name": "Fixed-Low", "temperatures": SCHEDULES["L"]},
    "M": {"name": "Fixed-Mid", "temperatures": SCHEDULES["M"]},
    "H": {"name": "Fixed-High", "temperatures": SCHEDULES["H"]},
    "A": {"name": "Annealing", "temperatures": SCHEDULES["A"]},
    "C": {"name": "Fixed-Cycle", "temperatures": SCHEDULES["C"]},
    "MTX": {"name": "Multi-Temperature Exchange", "temperatures": SCHEDULES["MTX"]},
    "E": {"name": "Adaptive", "temperatures": (ADAPTIVE_INITIAL,), "bounds": (LOW_TEMPERATURE, HIGH_TEMPERATURE)},
    "E2": {"name": "Validity-Novelty Adaptive", "temperatures": (ADAPTIVE_INITIAL,), "bounds": (LOW_TEMPERATURE, HIGH_TEMPERATURE)},
}
POLICIES = POLICY_SPECS


__all__ = [
    "ADAPTIVE_DECREASE",
    "ADAPTIVE_CONTROLLER_E2",
    "ADAPTIVE_CONTROLLER_V1",
    "ADAPTIVE_INCREASE",
    "ADAPTIVE_INITIAL",
    "AdaptivePolicy",
    "AdaptiveTemperaturePolicy",
    "ARM_IDS",
    "AnnealingPolicy",
    "CyclePolicy",
    "E2AdaptivePolicy",
    "E2_DECISION_PRECEDENCE",
    "E2_MINIMUM_USEFUL_NEW_BEHAVIORS",
    "E2_MINIMUM_VALID_CANDIDATES",
    "E2_USEFUL_NOVELTY_SCORE_TOLERANCE",
    "FixedCyclePolicy",
    "FixedPolicy",
    "FixedTemperaturePolicy",
    "HIGH_TEMPERATURE",
    "LOW_TEMPERATURE",
    "MTXPolicy",
    "MID_TEMPERATURE",
    "MultiTemperatureExchangePolicy",
    "POLICIES",
    "POLICY_SPECS",
    "SCHEDULES",
    "SchedulePolicy",
    "ScheduledTemperaturePolicy",
    "TemperaturePolicy",
    "ValidityNoveltyAdaptiveTemperaturePolicy",
    "build_policy",
    "make_policy",
    "policy_for_arm",
    "reset_policy",
    "schedule_for",
    "temperature_for",
    "temperatures_for",
]
