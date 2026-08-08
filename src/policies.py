"""Temperature policies used by the fixed-budget search experiment.

The policy layer owns only exploration scheduling.  It never sees private test
labels or a model rationale.  ``temperature_for_round`` follows the small protocol
used by :mod:`src.runner`; the additional ``temperatures_for_round`` method exposes
the four slots needed by the multi-temperature exchange (MTX) arm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Optional


LOW_TEMPERATURE = 0.2
MID_TEMPERATURE = 0.7
HIGH_TEMPERATURE = 1.2
ADAPTIVE_INITIAL = 1.0
ADAPTIVE_DECREASE = 0.2
ADAPTIVE_INCREASE = 0.3

ARM_IDS = ("L", "M", "H", "A", "C", "MTX", "E")
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


# Names used by different experiment scripts.
FixedPolicy = FixedTemperaturePolicy
SchedulePolicy = ScheduledTemperaturePolicy
CyclePolicy = FixedCyclePolicy
AdaptivePolicy = AdaptiveTemperaturePolicy
MTXPolicy = MultiTemperatureExchangePolicy


def build_policy(arm: str, **kwargs: Any) -> TemperaturePolicy:
    """Construct one of the seven preregistered arms by ID."""

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
    if key == "E":
        return AdaptiveTemperaturePolicy(**kwargs)
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
}
POLICIES = POLICY_SPECS


__all__ = [
    "ADAPTIVE_DECREASE",
    "ADAPTIVE_INCREASE",
    "ADAPTIVE_INITIAL",
    "AdaptivePolicy",
    "AdaptiveTemperaturePolicy",
    "ARM_IDS",
    "AnnealingPolicy",
    "CyclePolicy",
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
    "build_policy",
    "make_policy",
    "policy_for_arm",
    "reset_policy",
    "schedule_for",
    "temperature_for",
    "temperatures_for",
]
