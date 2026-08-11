"""Small generation-only temperature diagnostic for the Kimi K3 route.

This is post-failure route calibration, not V3 development evidence.  It uses
three retired worlds, one fixed round-zero prompt per world, and 36 one-shot
requests.  No candidate is selected and the private test split is never read.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
from typing import Any

from .credentials import ProviderCredentials, load_provider_credentials
from .pilot_checkpoint import canonical_json_bytes, sha256_bytes, sha256_json
from .prompts import build_round_prompt
from .staged_pilot_v3 import AcceptedResponseContract, route_binding_sha256
from .v3_generation import generation_world_view
from .v3_live import build_v3_generator
from .verifier import Verifier


WORLD_SPECS = (
    {
        "world_seed": 1003,
        "depth": 3,
        "world_hash": "81c40b7e9511831e98ef4f45211b80d37e32c4bf803a131bacf267ab96222485",
        "temperature_cycle": (0.2, 0.7, 1.2),
    },
    {
        "world_seed": 1001,
        "depth": 4,
        "world_hash": "8fbad41d7e8683011aed4838219d56983d456fe07d0cd7c5da1adab079b822ec",
        "temperature_cycle": (0.7, 1.2, 0.2),
    },
    {
        "world_seed": 1002,
        "depth": 5,
        "world_hash": "604c7227ad4752c6905ee192769856ecc3f45dcec5b13e08cb8e012256ff5e54",
        "temperature_cycle": (1.2, 0.2, 0.7),
    },
)
TEMPERATURES = (0.2, 0.7, 1.2)
REPLICATES_PER_CELL = 4
TOTAL_CALLS = len(WORLD_SPECS) * len(TEMPERATURES) * REPLICATES_PER_CELL
POSITIVE_P_THRESHOLD = 0.10


class TemperatureDiagnosticError(RuntimeError):
    """The frozen diagnostic could not be completed exactly as specified."""


def frozen_schedule() -> tuple[dict[str, Any], ...]:
    """Return the balanced, time-interleaved 36-call schedule."""

    schedule: list[dict[str, Any]] = []
    for spec in WORLD_SPECS:
        replicate_by_temperature = {temperature: 0 for temperature in TEMPERATURES}
        for temperature in spec["temperature_cycle"] * REPLICATES_PER_CELL:
            replicate = replicate_by_temperature[temperature]
            replicate_by_temperature[temperature] += 1
            schedule.append(
                {
                    "world_seed": spec["world_seed"],
                    "depth": spec["depth"],
                    "temperature": temperature,
                    "replicate_index": replicate,
                }
            )
    if len(schedule) != TOTAL_CALLS:
        raise AssertionError("temperature diagnostic schedule drifted")
    return tuple(schedule)


def _contract_from_canary(
    path: str | Path,
    credentials: ProviderCredentials,
    generator: Any,
) -> tuple[AcceptedResponseContract, str]:
    try:
        raw = Path(path).read_bytes()
        artifact = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise TemperatureDiagnosticError("cannot load Kimi route canary") from exc
    if not isinstance(artifact, Mapping):
        raise TemperatureDiagnosticError("Kimi route canary is not an object")
    if (
        artifact.get("kind") != "v3-route-canary"
        or artifact.get("stratum_id") != "volcengine-kimi-k3"
        or artifact.get("passed") is not True
        or artifact.get("contract_satisfied") is not True
        or artifact.get("identity", {}).get("request_model") != credentials.model
        or credentials.model != "kimi-k3"
    ):
        raise TemperatureDiagnosticError("Kimi route canary identity is incompatible")
    value = artifact.get("accepted_response_contract")
    if not isinstance(value, Mapping):
        raise TemperatureDiagnosticError("Kimi route canary lacks response contract")
    try:
        contract = AcceptedResponseContract(
            provider_models=tuple(value["provider_models"]),
            finish_reasons=tuple(value["finish_reasons"]),
            max_output_tokens=value["max_output_tokens"],
            seed_supported=value["seed_supported"],
            require_zero_reasoning_tokens=value["require_zero_reasoning_tokens"],
            prompt_cache_mode=value["prompt_cache_mode"],
            provider_fingerprint_mode=value["provider_fingerprint_mode"],
            provider_fingerprint_sha256=value["provider_fingerprint_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TemperatureDiagnosticError("Kimi response contract is malformed") from exc
    if route_binding_sha256(generator, contract) != artifact.get(
        "route_binding_sha256"
    ):
        raise TemperatureDiagnosticError("Kimi route binding drifted from canary")
    return contract, sha256_bytes(raw)


def _unique(values: Sequence[str | None]) -> int:
    return len({value for value in values if isinstance(value, str) and value})


def _permutation_distribution(
    calls: Sequence[Mapping[str, Any]], field: str
) -> tuple[int, Counter[int]]:
    """Exact world-stratified randomization distribution for H-L unique yield."""

    observed = 0
    distributions: list[Counter[int]] = []
    for spec in WORLD_SPECS:
        world_calls = [
            call
            for call in calls
            if call["world_seed"] == spec["world_seed"]
            and call["temperature"] in {0.2, 1.2}
        ]
        if len(world_calls) != 8:
            raise TemperatureDiagnosticError("high/low cell accounting drifted")
        actual_high = [call[field] for call in world_calls if call["temperature"] == 1.2]
        actual_low = [call[field] for call in world_calls if call["temperature"] == 0.2]
        observed += _unique(actual_high) - _unique(actual_low)
        distribution: Counter[int] = Counter()
        indices = range(8)
        for high_indices in combinations(indices, 4):
            high_set = set(high_indices)
            high = [world_calls[index][field] for index in indices if index in high_set]
            low = [world_calls[index][field] for index in indices if index not in high_set]
            distribution[_unique(high) - _unique(low)] += 1
        distributions.append(distribution)
    joint: Counter[int] = Counter({0: 1})
    for distribution in distributions:
        combined: Counter[int] = Counter()
        for left, count_left in joint.items():
            for right, count_right in distribution.items():
                combined[left + right] += count_left * count_right
        joint = combined
    return observed, joint


def _metric_diagnostic(calls: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    observed, distribution = _permutation_distribution(calls, field)
    total = sum(distribution.values())
    per_world: list[dict[str, Any]] = []
    for spec in WORLD_SPECS:
        world_calls = [call for call in calls if call["world_seed"] == spec["world_seed"]]
        high = _unique(
            [call[field] for call in world_calls if call["temperature"] == 1.2]
        )
        low = _unique(
            [call[field] for call in world_calls if call["temperature"] == 0.2]
        )
        per_world.append(
            {
                "world_seed": spec["world_seed"],
                "high_unique": high,
                "low_unique": low,
                "delta_high_minus_low": high - low,
            }
        )
    return {
        "observed_delta_high_minus_low": observed,
        "positive_world_count": sum(row["delta_high_minus_low"] > 0 for row in per_world),
        "negative_world_count": sum(row["delta_high_minus_low"] < 0 for row in per_world),
        "per_world": per_world,
        "positive_one_sided_exact_p": sum(
            count for value, count in distribution.items() if value >= observed
        )
        / total,
        "negative_one_sided_exact_p": sum(
            count for value, count in distribution.items() if value <= observed
        )
        / total,
        "permutation_count": total,
    }


def summarize_calls(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(calls) != TOTAL_CALLS:
        raise TemperatureDiagnosticError("diagnostic requires exactly 36 calls")
    cells: list[dict[str, Any]] = []
    for spec in WORLD_SPECS:
        for temperature in TEMPERATURES:
            selected = [
                call
                for call in calls
                if call["world_seed"] == spec["world_seed"]
                and call["temperature"] == temperature
            ]
            if len(selected) != REPLICATES_PER_CELL:
                raise TemperatureDiagnosticError("diagnostic cell accounting drifted")
            cells.append(
                {
                    "world_seed": spec["world_seed"],
                    "depth": spec["depth"],
                    "temperature": temperature,
                    "planned_calls": REPLICATES_PER_CELL,
                    "outer_schema_valid_count": sum(
                        call["outer_schema_valid"] for call in selected
                    ),
                    "search_valid_count": sum(call["search_valid"] for call in selected),
                    "canonical_unique_count": _unique(
                        [call["canonical_hash"] for call in selected]
                    ),
                    "behavioral_unique_count": _unique(
                        [call["behavior_hash"] for call in selected]
                    ),
                }
            )
    canonical = _metric_diagnostic(calls, "canonical_hash")
    behavioral = _metric_diagnostic(calls, "behavior_hash")
    positive = all(
        metric["observed_delta_high_minus_low"] > 0
        and metric["positive_world_count"] >= 2
        and metric["positive_one_sided_exact_p"] <= POSITIVE_P_THRESHOLD
        for metric in (canonical, behavioral)
    )
    negative = all(
        metric["observed_delta_high_minus_low"] < 0
        and metric["negative_world_count"] >= 2
        and metric["negative_one_sided_exact_p"] <= POSITIVE_P_THRESHOLD
        for metric in (canonical, behavioral)
    )
    if positive:
        classification = "temperature_effect_directionally_supported"
    elif negative:
        classification = "temperature_effect_directionally_reversed"
    else:
        classification = "needs_stage_b"
    return {
        "cells": cells,
        "overall": {
            "planned_calls": TOTAL_CALLS,
            "outer_schema_valid_count": sum(call["outer_schema_valid"] for call in calls),
            "search_valid_count": sum(call["search_valid"] for call in calls),
            "input_tokens": sum(call["input_tokens"] for call in calls),
            "output_tokens": sum(call["output_tokens"] for call in calls),
            "latency_ms": sum(float(call["latency_ms"]) for call in calls),
        },
        "canonical_high_vs_low": canonical,
        "behavioral_high_vs_low": behavioral,
        "stage_a_classification": classification,
    }


def run_temperature_diagnostic(
    credentials: ProviderCredentials,
    *,
    canary_path: str | Path,
    generator: Any | None = None,
) -> dict[str, Any]:
    """Execute all 36 one-shot calls or raise without returning an artifact."""

    live = build_v3_generator(credentials) if generator is None else generator
    contract, canary_sha256 = _contract_from_canary(canary_path, credentials, live)
    schedule = frozen_schedule()
    views: dict[int, Any] = {}
    prompts: dict[int, str] = {}
    verifiers: dict[int, Verifier] = {}
    for spec in WORLD_SPECS:
        view = generation_world_view(spec)
        if hasattr(view, "test") or hasattr(view, "law"):
            raise TemperatureDiagnosticError("generation world view exposed private state")
        views[spec["world_seed"]] = view
        prompts[spec["world_seed"]] = build_round_prompt(
            view, round_index=0, archive=(), counterexamples=()
        )
        verifiers[spec["world_seed"]] = Verifier(view, counterexample_limit=0)

    calls: list[dict[str, Any]] = []
    for ordinal, slot in enumerate(schedule):
        seed = slot["world_seed"]
        prompt = prompts[seed]
        # The provider adapter is one-shot.  Any transport or response-contract
        # exception aborts this diagnostic; failed calls are never topped up.
        response = live.generate(
            prompt,
            temperature=slot["temperature"],
            max_output_tokens=256,
            round_index=0,
            candidate_index=slot["replicate_index"],
            seed=None,
        )
        contract.validate(response)
        verified = verifiers[seed].verify_probe(
            response.expression, counterexample_limit=0
        )
        outer_valid = response.candidate_format == "json_expression"
        search_valid = bool(
            outer_valid and verified.syntax_valid and verified.runtime_valid
        )
        failure_code = None
        if not outer_valid:
            failure_code = "outer_schema"
        elif not search_valid:
            failure_code = (
                verified.failure_codes[0]
                if verified.failure_codes
                else "search_invalid"
            )
        calls.append(
            {
                "ordinal": ordinal,
                **slot,
                "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
                "outer_schema_valid": outer_valid,
                "search_valid": search_valid,
                "canonical_hash": verified.canonical_hash if search_valid else None,
                "behavior_hash": verified.behavior_hash if search_valid else None,
                "failure_code": failure_code,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "latency_ms": float(response.latency_ms),
                "finish_reason": response.finish_reason,
                "provider_model": response.provider_model,
            }
        )

    rules = {
        "scope": "post_failure_retired_world_route_calibration",
        "calls": TOTAL_CALLS,
        "temperatures": list(TEMPERATURES),
        "replicates_per_world_temperature": REPLICATES_PER_CELL,
        "unit_for_direction": "world",
        "primary_comparison": "temperature_1.2_minus_0.2_unique_yield",
        "positive_p_threshold": POSITIVE_P_THRESHOLD,
        "positive_rule": "both metrics delta>0, >=2/3 positive worlds, exact one-sided p<=0.10",
        "negative_rule": "symmetric negative rule",
        "middle_temperature_role": "descriptive_gradient_only",
        "transport_failure_rule": "abort_without_top_up",
        "primary_v3_result_changed": False,
        "private_test_evaluated": False,
    }
    return {
        "schema_version": 1,
        "kind": "kimi-k3-temperature-diagnostic",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence": False,
        "evidence_scope": "route-calibration-only",
        "identity": {
            "request_model": credentials.model,
            "provider_models": list(contract.provider_models),
            "route_binding_sha256": route_binding_sha256(live, contract),
            "canary_sha256": canary_sha256,
            "diagnostic_implementation_sha256": sha256_bytes(Path(__file__).read_bytes()),
        },
        "rules": rules,
        "plan_sha256": sha256_json({"worlds": WORLD_SPECS, "schedule": schedule, "rules": rules}),
        "calls": calls,
        "summary": summarize_calls(calls),
        "caveats": [
            "The 36 calls are not 36 independent worlds.",
            "This route diagnostic cannot revive the failed Kimi compatibility screen.",
            "A positive result can only motivate a newly frozen development campaign.",
        ],
    }


def write_new_artifact(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json_bytes(payload) + b"\n"
    try:
        with target.open("xb") as handle:
            handle.write(encoded)
    except FileExistsError as exc:
        raise TemperatureDiagnosticError("refusing to overwrite diagnostic artifact") from exc
    target.chmod(0o600)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--env-prefix", default="HERMES")
    parser.add_argument("--canary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute:
        raise TemperatureDiagnosticError("refusing paid diagnostic without --execute")
    credentials = load_provider_credentials(prefix=args.env_prefix, env_file=args.env_file)
    result = run_temperature_diagnostic(
        credentials,
        canary_path=args.canary,
    )
    target = write_new_artifact(args.output, result)
    print(
        json.dumps(
            {
                "status": "complete",
                "output": str(target.resolve()),
                "stage_a_classification": result["summary"]["stage_a_classification"],
                "private_test_evaluated": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "TemperatureDiagnosticError",
    "frozen_schedule",
    "run_temperature_diagnostic",
    "summarize_calls",
    "write_new_artifact",
]
