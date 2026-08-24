"""Sealed-plan and blind science coordinator for fair-choice strong K4.

This is intentionally a small research coordinator, not an authentication or
permissions system.  Exact byte digests make the preregistered inputs and the
completed 192-call attempt reproducible.  The live path can read the public
manifest and route canaries, but its API has no private-key argument.  The
private key is first opened by the offline analyzer after the complete joint
generation bundle has passed validation.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import itertools
import json
import os
from pathlib import Path
from typing import Any

from .credentials import ProviderCredentials, load_provider_credentials
from .provenance import PROJECT_ROOT, source_manifest
from .providers.openai_compatible import OpenAICompatibleGenerator
from .runner import GenerationResponse
from .spark_strong_k4_benchmark import (
    CANONICAL_ROUTE_IDS,
    FAIR_CONFIG_FILE_SHA256,
    FAIR_CONFIG_PATH,
    PAIR_COUNT,
    PROTOCOL_ID,
    TASK_COUNT,
)
from . import spark_strong_k4_benchmark as benchmark
from . import spark_strong_k4_canary as canary
from .staged_pilot_v3 import (
    AcceptedResponseContract,
    V3ResponseContractError,
    route_binding_sha256,
)
from .v3_live import build_v3_generator


SCHEMA_VERSION = 1
FORMAL_PLAN_KIND = "spark-strong-k4-fair-choice-formal-plan"
GENERATION_BUNDLE_KIND = "spark-strong-k4-fair-choice-generation-bundle"
JOINT_ANALYSIS_KIND = "spark-strong-k4-fair-choice-joint-analysis"
FORMAL_PLAN_FILE_NAME = "formal-plan.json"
PUBLIC_FILE_NAME = "public.json"
PRIVATE_FILE_NAME = "private.json"
EVIDENCE_SCOPE = (
    "outcome_conditioned_finite_dsl_prompt_level_matched_sham_mechanism_test"
)

SCIENCE_TEMPERATURE = 0.2
SCIENCE_MAX_OUTPUT_TOKENS = 256
SCIENCE_THINKING = "disabled"
SCIENCE_CALLS_PER_ROUTE = TASK_COUNT
SCIENCE_TOTAL_CALLS = TASK_COUNT * len(CANONICAL_ROUTE_IDS)

# itertools.permutations over the canonical A/B/C route order gives
# ABC, ACB, BAC, BCA, CAB, CBA.  The multiplicities below are the frozen
# compromise for 64 tasks: every pairwise route order is exactly 32/32, while
# route-position counts differ by at most two (strict 32/32 and spread <= 1
# cannot both be achieved arithmetically).
ROUTE_PERMUTATION_MULTIPLICITIES = (10, 11, 11, 11, 11, 10)


class FairChoiceFormalError(ValueError):
    """A formal input, execution contract, or completed bundle is malformed."""


@dataclass(frozen=True)
class FairChoiceSciencePreflight:
    """Validated public-only state returned before the first provider call."""

    plan: dict[str, Any]
    public_manifest: dict[str, Any]
    canary_plan: dict[str, Any]
    generators: dict[str, OpenAICompatibleGenerator]
    response_contracts: dict[str, AcceptedResponseContract]


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FairChoiceFormalError(
            "formal values must be finite canonical JSON"
        ) from exc


def _render_json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FairChoiceFormalError("formal file is not finite JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def fair_choice_formal_file_sha256(path: str | Path) -> str:
    try:
        return _sha256_bytes(Path(path).read_bytes())
    except OSError as exc:
        raise FairChoiceFormalError(f"cannot read formal file {path}") from exc


def write_fair_choice_formal_json_exclusive(
    value: Mapping[str, Any], output: str | Path
) -> str:
    """Write deterministic JSON once with mode 0600 and return its byte hash."""

    if not isinstance(value, Mapping):
        raise FairChoiceFormalError("formal JSON output must be an object")
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = _render_json_bytes(value)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise FairChoiceFormalError(
            f"refusing to overwrite formal file {path}"
        ) from exc
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        # Keep a partial exclusive output visible rather than silently replacing it.
        raise
    return _sha256_bytes(rendered)


def _read_json_file(path: str | Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    try:
        raw = source.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FairChoiceFormalError(f"cannot read {label} JSON {source}") from exc
    if not isinstance(value, dict):
        raise FairChoiceFormalError(f"{label} JSON must contain one object")
    return value, raw


def _read_bound_json(
    path: str | Path, expected_file_sha256: str, *, label: str
) -> tuple[dict[str, Any], bytes]:
    if not _is_sha256(expected_file_sha256):
        raise FairChoiceFormalError(f"expected {label} file SHA-256 is malformed")
    value, raw = _read_json_file(path, label=label)
    if _sha256_bytes(raw) != expected_file_sha256:
        raise FairChoiceFormalError(f"{label} bytes differ from the sealed plan")
    return value, raw


def _accepted_response_contract(value: object) -> AcceptedResponseContract:
    expected = {
        "provider_models",
        "finish_reasons",
        "max_output_tokens",
        "seed_supported",
        "require_zero_reasoning_tokens",
        "prompt_cache_mode",
        "provider_fingerprint_mode",
        "provider_fingerprint_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise FairChoiceFormalError("accepted response contract fields are malformed")
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
    except (TypeError, ValueError) as exc:
        raise FairChoiceFormalError("accepted response contract is malformed") from exc
    if contract.to_dict() != dict(value):
        raise FairChoiceFormalError("accepted response contract is not canonical")
    return contract


def _planned_route_values() -> tuple[dict[str, str], Mapping[str, Any]]:
    config = benchmark._load_frozen_fair_config()
    planned = config.get("planned_routes")
    if not isinstance(planned, Mapping):
        raise FairChoiceFormalError("frozen planned_routes section is malformed")
    aliases = planned.get("request_model_aliases")
    if not isinstance(aliases, Mapping):
        raise FairChoiceFormalError("frozen request model aliases are malformed")
    canonical_aliases = {
        route_id: aliases.get(route_id) for route_id in CANONICAL_ROUTE_IDS
    }
    if (
        planned.get("route_ids") != list(CANONICAL_ROUTE_IDS)
        or any(
            not isinstance(value, str) or not value
            for value in canonical_aliases.values()
        )
        or planned.get("route_count") != len(CANONICAL_ROUTE_IDS)
        or planned.get("logical_calls_per_route") != SCIENCE_CALLS_PER_ROUTE
        or planned.get("total_logical_calls") != SCIENCE_TOTAL_CALLS
        or planned.get("temperature") != SCIENCE_TEMPERATURE
        or planned.get("max_output_tokens") != SCIENCE_MAX_OUTPUT_TOKENS
        or planned.get("thinking") != SCIENCE_THINKING
    ):
        raise FairChoiceFormalError("formal execution differs from frozen fair config")
    return canonical_aliases, planned


def _permutation_sequence() -> list[int]:
    """Merge centered quantiles for the six frozen permutation counts."""

    events: list[tuple[Fraction, int, int]] = []
    for permutation_index, count in enumerate(ROUTE_PERMUTATION_MULTIPLICITIES):
        for occurrence in range(count):
            events.append(
                (
                    Fraction(2 * occurrence + 1, 2 * count),
                    permutation_index,
                    occurrence,
                )
            )
    events.sort()
    sequence = [permutation_index for _, permutation_index, _ in events]
    if len(sequence) != TASK_COUNT:
        raise AssertionError("route permutation sequence must have 64 entries")
    return sequence


def _schedule_balance_audit(
    route_orders: Sequence[Sequence[str]],
) -> dict[str, Any]:
    permutations = list(itertools.permutations(CANONICAL_ROUTE_IDS))
    labels = [">".join(permutation) for permutation in permutations]
    counts = Counter(">".join(order) for order in route_orders)
    position_counts = {
        route_id: [
            sum(order[position] == route_id for order in route_orders)
            for position in range(len(CANONICAL_ROUTE_IDS))
        ]
        for route_id in CANONICAL_ROUTE_IDS
    }
    pairwise: dict[str, int] = {}
    for left_index, left in enumerate(CANONICAL_ROUTE_IDS):
        for right in CANONICAL_ROUTE_IDS[left_index + 1 :]:
            pairwise[f"{left}_before_{right}"] = sum(
                order.index(left) < order.index(right) for order in route_orders
            )
            pairwise[f"{right}_before_{left}"] = sum(
                order.index(right) < order.index(left) for order in route_orders
            )
    return {
        "permutation_counts": {label: counts[label] for label in labels},
        "route_position_counts": position_counts,
        "maximum_route_position_count_spread": max(
            max(values) - min(values) for values in position_counts.values()
        ),
        "pairwise_order_counts": pairwise,
        "pairwise_orders_are_exactly_balanced_32_32": all(
            value == TASK_COUNT // 2 for value in pairwise.values()
        ),
        "tradeoff": (
            "exact pairwise 32/32; route-position counts have the minimum "
            "frozen spread accepted for this schedule (maximum two)"
        ),
    }


def build_balanced_fair_choice_schedule(
    task_ids: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the fixed output-independent 64-task by three-route schedule."""

    if (
        not isinstance(task_ids, Sequence)
        or isinstance(task_ids, (str, bytes))
        or len(task_ids) != TASK_COUNT
        or any(not isinstance(task_id, str) or not task_id for task_id in task_ids)
        or len(set(task_ids)) != TASK_COUNT
    ):
        raise FairChoiceFormalError("schedule requires 64 unique public task ids")
    permutations = list(itertools.permutations(CANONICAL_ROUTE_IDS))
    sequence = _permutation_sequence()
    route_orders = [permutations[index] for index in sequence]
    schedule: list[dict[str, Any]] = []
    for task_ordinal, (task_id, order) in enumerate(
        zip(task_ids, route_orders, strict=True)
    ):
        for route_position, route_id in enumerate(order):
            schedule.append(
                {
                    "call_index": len(schedule),
                    "task_ordinal": task_ordinal,
                    "task_id": task_id,
                    "route_id": route_id,
                    "route_position": route_position,
                }
            )
    return schedule, _schedule_balance_audit(route_orders)


def _public_private_bijection_sha256(
    public_manifest: Mapping[str, Any], private_key: Mapping[str, Any]
) -> str:
    public_ids = [task["task_id"] for task in public_manifest["tasks"]]
    private_rows: list[dict[str, str]] = []
    for pair in private_key["pairs"]:
        for arm in benchmark.ARMS:
            private_rows.append(
                {
                    "task_id": pair["arms"][arm]["task_id"],
                    "pair_id": pair["pair_id"],
                    "arm": arm,
                }
            )
    private_ids = [row["task_id"] for row in private_rows]
    if (
        len(public_ids) != TASK_COUNT
        or len(private_ids) != TASK_COUNT
        or len(set(private_ids)) != TASK_COUNT
        or set(public_ids) != set(private_ids)
    ):
        raise FairChoiceFormalError(
            "public/private task ids are not a 64-task bijection"
        )
    return _sha256_json(
        {
            "protocol_id": PROTOCOL_ID,
            "task_count": TASK_COUNT,
            "public_task_sequence": public_ids,
            "private_task_bindings_sorted_by_task_id": sorted(
                private_rows, key=lambda row: row["task_id"]
            ),
        }
    )


def _canary_qualification(
    artifact: Mapping[str, Any], *, file_sha256: str
) -> dict[str, Any]:
    return {
        "route_id": artifact["route_id"],
        "passed": artifact["passed"],
        "canary_artifact_file_sha256": file_sha256,
        "canary_artifact_sha256": artifact["canary_artifact_sha256"],
        "request_model": artifact["request_model"],
        "response_model": artifact["response_model"],
        "sanitized_request_contract": json.loads(
            _canonical_json_bytes(artifact["sanitized_request_contract"])
        ),
        "accepted_response_contract": json.loads(
            _canonical_json_bytes(artifact["accepted_response_contract"])
        ),
        "route_binding_sha256": artifact["route_binding_sha256"],
    }


def build_fair_choice_formal_plan(
    public_manifest: Mapping[str, Any],
    private_key: Mapping[str, Any],
    *,
    canary_plan_path: str | Path,
    canary_artifact_paths: Mapping[str, str | Path],
) -> dict[str, Any]:
    """Build an in-memory formal plan from deterministic benchmark objects."""

    benchmark.validate_public_manifest(public_manifest)
    benchmark.validate_private_key(private_key, public_manifest)
    current_source_sha256 = source_manifest(PROJECT_ROOT)["source_manifest_sha256"]
    config_sha256 = benchmark.fair_config_file_sha256()
    if (
        public_manifest["current_source_manifest_sha256"] != current_source_sha256
        or private_key["sealed_input_identity"]["current_source_manifest_sha256"]
        != current_source_sha256
        or public_manifest["fair_config_file_sha256"] != config_sha256
        or private_key["sealed_input_identity"]["fair_config_file_sha256"]
        != config_sha256
    ):
        raise FairChoiceFormalError(
            "benchmark objects differ from current source/config"
        )

    canary_plan, canary_plan_raw = _read_json_file(
        canary_plan_path, label="canary plan"
    )
    canary.validate_fair_choice_canary_plan(canary_plan)
    if (
        canary_plan["current_source_manifest_sha256"] != current_source_sha256
        or canary_plan["fair_config_file_sha256"] != config_sha256
    ):
        raise FairChoiceFormalError("canary plan differs from current source/config")
    if set(canary_artifact_paths) != set(CANONICAL_ROUTE_IDS):
        raise FairChoiceFormalError(
            "exactly three canonical canary artifacts are required"
        )
    qualifications: list[dict[str, Any]] = []
    for route_id in CANONICAL_ROUTE_IDS:
        artifact, raw = _read_json_file(
            canary_artifact_paths[route_id], label=f"{route_id} canary artifact"
        )
        canary.validate_fair_choice_canary_artifact(canary_plan, artifact)
        if artifact.get("route_id") != route_id or artifact.get("passed") is not True:
            raise FairChoiceFormalError(f"{route_id} canary did not pass")
        if artifact.get("current_source_manifest_sha256") != current_source_sha256:
            raise FairChoiceFormalError(f"{route_id} canary source identity drifted")
        qualifications.append(
            _canary_qualification(artifact, file_sha256=_sha256_bytes(raw))
        )

    aliases, _planned = _planned_route_values()
    for qualification in qualifications:
        if qualification["request_model"] != aliases[qualification["route_id"]]:
            raise FairChoiceFormalError(
                "canary request alias differs from frozen config"
            )

    public_bytes = _render_json_bytes(public_manifest)
    private_bytes = _render_json_bytes(private_key)
    task_ids = [task["task_id"] for task in public_manifest["tasks"]]
    schedule, balance = build_balanced_fair_choice_schedule(task_ids)
    bijection_sha256 = _public_private_bijection_sha256(
        public_manifest, private_key
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": FORMAL_PLAN_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence_scope": EVIDENCE_SCOPE,
        "current_source_manifest_sha256": current_source_sha256,
        "file_bindings": {
            "fair_config": {
                "relative_path": FAIR_CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix(),
                "file_sha256": config_sha256,
            },
            "public_manifest": {
                "file_name": PUBLIC_FILE_NAME,
                "file_sha256": _sha256_bytes(public_bytes),
                "inner_sha256": public_manifest["public_manifest_sha256"],
            },
            "private_key": {
                "file_name": PRIVATE_FILE_NAME,
                "file_sha256": _sha256_bytes(private_bytes),
                "inner_sha256": private_key["private_key_sha256"],
            },
            "canary_plan": {
                "file_sha256": _sha256_bytes(canary_plan_raw),
                "inner_sha256": canary_plan["canary_plan_sha256"],
                "canary_id": canary_plan["canary_id"],
                "prompt_set_sha256": canary_plan["prompt_set_sha256"],
            },
        },
        "benchmark_binding": {
            "pair_count": PAIR_COUNT,
            "task_count": TASK_COUNT,
            "private_design_commitment_sha256": public_manifest[
                "private_design_commitment_sha256"
            ],
            "public_private_task_bijection_sha256": bijection_sha256,
            "public_task_sequence_sha256": _sha256_json(task_ids),
        },
        "route_qualifications": qualifications,
        "execution": {
            "route_ids": list(CANONICAL_ROUTE_IDS),
            "request_model_aliases": aliases,
            "temperature": SCIENCE_TEMPERATURE,
            "max_output_tokens": SCIENCE_MAX_OUTPUT_TOKENS,
            "thinking": SCIENCE_THINKING,
            "logical_calls_per_route": SCIENCE_CALLS_PER_ROUTE,
            "total_logical_calls": SCIENCE_TOTAL_CALLS,
            "physical_attempts_per_task_route": 1,
            "retry_or_resume": False,
            "provider_message": "rendered_prompt_only",
            "schedule_rule": (
                "centered-quantile merge of ABC,ACB,BAC,BCA,CAB,CBA "
                "multiplicities 10,11,11,11,11,10 over public task order"
            ),
            "route_permutation_multiplicities": list(
                ROUTE_PERMUTATION_MULTIPLICITIES
            ),
            "schedule": schedule,
            "schedule_sha256": _sha256_json(schedule),
            "balance_audit": balance,
        },
        "analysis_barrier": {
            "generation_may_read_private_key": False,
            "all_three_complete_route_artifacts_required": True,
            "private_key_read_only_after_complete_bundle_validation": True,
            "invalid_completed_content_is_miss_without_retry": True,
            "transport_failure_makes_attempt_incomplete": True,
            "holm_family_is_all_three_canonical_routes": True,
        },
    }
    plan = {**unsigned, "formal_plan_sha256": _sha256_json(unsigned)}
    validate_fair_choice_formal_plan(plan)
    return plan


def _route_binding_from_contracts(
    request_contract: Mapping[str, Any], response_contract: Mapping[str, Any]
) -> str:
    # This is the same public derivation used by staged_pilot_v3.route_binding_sha256.
    from . import staged_pilot_v3 as staged_v3

    return _sha256_json(
        {
            "coordinator_version": staged_v3.V3_COORDINATOR_VERSION,
            "accepted_attempt_estimand": staged_v3.V3_ACCEPTED_ATTEMPT_ESTIMAND,
            "request_contract": dict(request_contract),
            "response_contract": dict(response_contract),
        }
    )


def validate_fair_choice_formal_plan(plan: Mapping[str, Any]) -> None:
    """Validate the closed formal-plan schema and exact frozen schedule."""

    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence_scope",
        "current_source_manifest_sha256",
        "file_bindings",
        "benchmark_binding",
        "route_qualifications",
        "execution",
        "analysis_barrier",
        "formal_plan_sha256",
    }
    if not isinstance(plan, Mapping) or set(plan) != expected_top:
        raise FairChoiceFormalError("formal plan uses a non-canonical schema")
    unsigned = {
        key: value for key, value in plan.items() if key != "formal_plan_sha256"
    }
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("kind") != FORMAL_PLAN_KIND
        or plan.get("protocol_id") != PROTOCOL_ID
        or plan.get("evidence_scope") != EVIDENCE_SCOPE
        or not _is_sha256(plan.get("current_source_manifest_sha256"))
        or plan.get("formal_plan_sha256") != _sha256_json(unsigned)
    ):
        raise FairChoiceFormalError("formal plan identity or self-digest is malformed")

    files = plan.get("file_bindings")
    if not isinstance(files, Mapping) or set(files) != {
        "fair_config",
        "public_manifest",
        "private_key",
        "canary_plan",
    }:
        raise FairChoiceFormalError("formal file bindings are malformed")
    expected_file_fields = {
        "fair_config": {"relative_path", "file_sha256"},
        "public_manifest": {"file_name", "file_sha256", "inner_sha256"},
        "private_key": {"file_name", "file_sha256", "inner_sha256"},
        "canary_plan": {
            "file_sha256",
            "inner_sha256",
            "canary_id",
            "prompt_set_sha256",
        },
    }
    for name, fields in expected_file_fields.items():
        binding = files.get(name)
        if (
            not isinstance(binding, Mapping)
            or set(binding) != fields
            or any(
                not _is_sha256(binding[field])
                for field in fields
                if field.endswith("sha256")
            )
        ):
            raise FairChoiceFormalError(f"formal {name} file binding is malformed")
    if (
        files["fair_config"]["relative_path"]
        != FAIR_CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix()
        or files["fair_config"]["file_sha256"] != FAIR_CONFIG_FILE_SHA256
        or files["public_manifest"]["file_name"] != PUBLIC_FILE_NAME
        or files["private_key"]["file_name"] != PRIVATE_FILE_NAME
        or files["canary_plan"]["canary_id"] != canary.FAIR_CHOICE_CANARY_ID
    ):
        raise FairChoiceFormalError("formal file identities differ from fixed protocol")

    binding = plan.get("benchmark_binding")
    if (
        not isinstance(binding, Mapping)
        or set(binding)
        != {
            "pair_count",
            "task_count",
            "private_design_commitment_sha256",
            "public_private_task_bijection_sha256",
            "public_task_sequence_sha256",
        }
        or binding.get("pair_count") != PAIR_COUNT
        or binding.get("task_count") != TASK_COUNT
        or any(
            not _is_sha256(binding.get(field))
            for field in (
                "private_design_commitment_sha256",
                "public_private_task_bijection_sha256",
                "public_task_sequence_sha256",
            )
        )
    ):
        raise FairChoiceFormalError("formal benchmark binding is malformed")

    routes = plan.get("route_qualifications")
    qualification_fields = {
        "route_id",
        "passed",
        "canary_artifact_file_sha256",
        "canary_artifact_sha256",
        "request_model",
        "response_model",
        "sanitized_request_contract",
        "accepted_response_contract",
        "route_binding_sha256",
    }
    if (
        not isinstance(routes, list)
        or [route.get("route_id") for route in routes]
        != list(CANONICAL_ROUTE_IDS)
    ):
        raise FairChoiceFormalError("formal route qualifications are incomplete")
    aliases, _planned = _planned_route_values()
    for route in routes:
        if (
            not isinstance(route, Mapping)
            or set(route) != qualification_fields
            or route.get("passed") is not True
            or route.get("request_model") != aliases[route["route_id"]]
            or not isinstance(route.get("response_model"), str)
            or not route["response_model"]
            or any(
                not _is_sha256(route.get(field))
                for field in (
                    "canary_artifact_file_sha256",
                    "canary_artifact_sha256",
                    "route_binding_sha256",
                )
            )
            or not isinstance(route.get("sanitized_request_contract"), Mapping)
        ):
            raise FairChoiceFormalError("formal route qualification is malformed")
        contract = _accepted_response_contract(route["accepted_response_contract"])
        if (
            contract.provider_models != (route["response_model"],)
            or contract.seed_supported is not False
            or contract.max_output_tokens != SCIENCE_MAX_OUTPUT_TOKENS
            or not contract.require_zero_reasoning_tokens
            or route["route_binding_sha256"]
            != _route_binding_from_contracts(
                route["sanitized_request_contract"], contract.to_dict()
            )
        ):
            raise FairChoiceFormalError("formal route response binding is malformed")

    execution = plan.get("execution")
    execution_fields = {
        "route_ids",
        "request_model_aliases",
        "temperature",
        "max_output_tokens",
        "thinking",
        "logical_calls_per_route",
        "total_logical_calls",
        "physical_attempts_per_task_route",
        "retry_or_resume",
        "provider_message",
        "schedule_rule",
        "route_permutation_multiplicities",
        "schedule",
        "schedule_sha256",
        "balance_audit",
    }
    if not isinstance(execution, Mapping) or set(execution) != execution_fields:
        raise FairChoiceFormalError("formal execution fields are malformed")
    if (
        execution["route_ids"] != list(CANONICAL_ROUTE_IDS)
        or execution["request_model_aliases"] != aliases
        or execution["temperature"] != SCIENCE_TEMPERATURE
        or execution["max_output_tokens"] != SCIENCE_MAX_OUTPUT_TOKENS
        or execution["thinking"] != SCIENCE_THINKING
        or execution["logical_calls_per_route"] != SCIENCE_CALLS_PER_ROUTE
        or execution["total_logical_calls"] != SCIENCE_TOTAL_CALLS
        or execution["physical_attempts_per_task_route"] != 1
        or execution["retry_or_resume"] is not False
        or execution["provider_message"] != "rendered_prompt_only"
        or execution["route_permutation_multiplicities"]
        != list(ROUTE_PERMUTATION_MULTIPLICITIES)
    ):
        raise FairChoiceFormalError("formal execution parameters drifted")
    schedule = execution["schedule"]
    if not isinstance(schedule, list) or len(schedule) != SCIENCE_TOTAL_CALLS:
        raise FairChoiceFormalError("formal schedule must contain 192 calls")
    task_ids: list[str] = []
    schedule_fields = {
        "call_index",
        "task_ordinal",
        "task_id",
        "route_id",
        "route_position",
    }
    for task_ordinal in range(TASK_COUNT):
        group = schedule[task_ordinal * 3 : task_ordinal * 3 + 3]
        if len(group) != 3:
            raise FairChoiceFormalError("formal task schedule is incomplete")
        task_id = group[0].get("task_id") if isinstance(group[0], Mapping) else None
        if not isinstance(task_id, str) or not task_id:
            raise FairChoiceFormalError("formal schedule task id is malformed")
        task_ids.append(task_id)
        for route_position, row in enumerate(group):
            if (
                not isinstance(row, Mapping)
                or set(row) != schedule_fields
                or row["call_index"] != task_ordinal * 3 + route_position
                or row["task_ordinal"] != task_ordinal
                or row["task_id"] != task_id
                or row["route_position"] != route_position
            ):
                raise FairChoiceFormalError("formal schedule row is malformed")
    expected_schedule, expected_balance = build_balanced_fair_choice_schedule(task_ids)
    if (
        schedule != expected_schedule
        or execution["schedule_sha256"] != _sha256_json(schedule)
        or execution["balance_audit"] != expected_balance
        or binding["public_task_sequence_sha256"] != _sha256_json(task_ids)
    ):
        raise FairChoiceFormalError("formal schedule or balance audit drifted")

    barrier = plan.get("analysis_barrier")
    if barrier != {
        "generation_may_read_private_key": False,
        "all_three_complete_route_artifacts_required": True,
        "private_key_read_only_after_complete_bundle_validation": True,
        "invalid_completed_content_is_miss_without_retry": True,
        "transport_failure_makes_attempt_incomplete": True,
        "holm_family_is_all_three_canonical_routes": True,
    }:
        raise FairChoiceFormalError("formal analysis barrier drifted")


def materialize_fair_choice_formal(
    *,
    output_dir: str | Path,
    canary_plan_path: str | Path,
    canary_artifact_paths: Mapping[str, str | Path],
    sealed_result_path: str | Path = benchmark.SEALED_SCAN_PATH,
) -> dict[str, Any]:
    """Build and exclusively write public/private/formal-plan JSON files."""

    directory = Path(output_dir)
    outputs = {
        "public_manifest": directory / PUBLIC_FILE_NAME,
        "private_key": directory / PRIVATE_FILE_NAME,
        "formal_plan": directory / FORMAL_PLAN_FILE_NAME,
    }
    if any(os.path.lexists(path) for path in outputs.values()):
        raise FairChoiceFormalError("refusing to overwrite a formal output file")
    sealed_result = benchmark.load_sealed_scan_result(sealed_result_path)
    public_manifest, private_key = benchmark.build_fair_choice_benchmark(sealed_result)
    plan = build_fair_choice_formal_plan(
        public_manifest,
        private_key,
        canary_plan_path=canary_plan_path,
        canary_artifact_paths=canary_artifact_paths,
    )
    observed_public = write_fair_choice_formal_json_exclusive(
        public_manifest, outputs["public_manifest"]
    )
    observed_private = write_fair_choice_formal_json_exclusive(
        private_key, outputs["private_key"]
    )
    observed_plan = write_fair_choice_formal_json_exclusive(
        plan, outputs["formal_plan"]
    )
    if (
        observed_public
        != plan["file_bindings"]["public_manifest"]["file_sha256"]
        or observed_private != plan["file_bindings"]["private_key"]["file_sha256"]
    ):
        raise FairChoiceFormalError("materialized benchmark bytes differ from plan")
    return {
        "outputs": {name: str(path) for name, path in outputs.items()},
        "file_sha256": {
            "public_manifest": observed_public,
            "private_key": observed_private,
            "formal_plan": observed_plan,
        },
        "formal_plan_sha256": plan["formal_plan_sha256"],
    }


def _qualification_by_route(plan: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {route["route_id"]: route for route in plan["route_qualifications"]}


def preflight_fair_choice_science(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    public_manifest_path: str | Path,
    canary_plan_path: str | Path,
    canary_artifact_paths: Mapping[str, str | Path],
    generators: Mapping[str, OpenAICompatibleGenerator],
) -> FairChoiceSciencePreflight:
    """Validate every public identity and all three adapters before any call."""

    plan, _plan_raw = _read_bound_json(
        plan_path, expected_plan_file_sha256, label="formal plan"
    )
    validate_fair_choice_formal_plan(plan)
    current_source_sha256 = source_manifest(PROJECT_ROOT)["source_manifest_sha256"]
    if current_source_sha256 != plan["current_source_manifest_sha256"]:
        raise FairChoiceFormalError("current source differs from the formal plan")
    if benchmark.fair_config_file_sha256() != plan["file_bindings"]["fair_config"][
        "file_sha256"
    ]:
        raise FairChoiceFormalError("fair config differs from the formal plan")

    public_manifest, _public_raw = _read_bound_json(
        public_manifest_path,
        plan["file_bindings"]["public_manifest"]["file_sha256"],
        label="public manifest",
    )
    benchmark.validate_public_manifest(public_manifest)
    if (
        public_manifest["public_manifest_sha256"]
        != plan["file_bindings"]["public_manifest"]["inner_sha256"]
        or public_manifest["current_source_manifest_sha256"]
        != current_source_sha256
        or public_manifest["private_design_commitment_sha256"]
        != plan["benchmark_binding"]["private_design_commitment_sha256"]
        or _sha256_json([task["task_id"] for task in public_manifest["tasks"]])
        != plan["benchmark_binding"]["public_task_sequence_sha256"]
    ):
        raise FairChoiceFormalError("public manifest identity differs from formal plan")

    canary_plan, _canary_plan_raw = _read_bound_json(
        canary_plan_path,
        plan["file_bindings"]["canary_plan"]["file_sha256"],
        label="canary plan",
    )
    canary.validate_fair_choice_canary_plan(canary_plan)
    if (
        canary_plan["canary_plan_sha256"]
        != plan["file_bindings"]["canary_plan"]["inner_sha256"]
        or canary_plan["prompt_set_sha256"]
        != plan["file_bindings"]["canary_plan"]["prompt_set_sha256"]
    ):
        raise FairChoiceFormalError("canary plan identity differs from formal plan")
    if set(canary_artifact_paths) != set(CANONICAL_ROUTE_IDS):
        raise FairChoiceFormalError("preflight requires all three canary artifacts")
    if set(generators) != set(CANONICAL_ROUTE_IDS):
        raise FairChoiceFormalError("preflight requires all three canonical generators")

    qualifications = _qualification_by_route(plan)
    response_contracts: dict[str, AcceptedResponseContract] = {}
    canonical_generators: dict[str, OpenAICompatibleGenerator] = {}
    for route_id in CANONICAL_ROUTE_IDS:
        qualification = qualifications[route_id]
        artifact, _artifact_raw = _read_bound_json(
            canary_artifact_paths[route_id],
            qualification["canary_artifact_file_sha256"],
            label=f"{route_id} canary artifact",
        )
        canary.validate_fair_choice_canary_artifact(canary_plan, artifact)
        if (
            artifact.get("passed") is not True
            or _canary_qualification(
                artifact,
                file_sha256=qualification["canary_artifact_file_sha256"],
            )
            != qualification
        ):
            raise FairChoiceFormalError(f"{route_id} canary qualification drifted")
        generator = generators[route_id]
        if type(generator) is not OpenAICompatibleGenerator:
            raise FairChoiceFormalError(
                "science requires exact OpenAICompatibleGenerator adapters"
            )
        canary.preflight_fair_choice_route(canary_plan, route_id, generator)
        if generator.sanitized_request_contract() != qualification[
            "sanitized_request_contract"
        ]:
            raise FairChoiceFormalError(f"{route_id} runtime request contract drifted")
        contract = _accepted_response_contract(
            qualification["accepted_response_contract"]
        )
        if route_binding_sha256(generator, contract) != qualification[
            "route_binding_sha256"
        ]:
            raise FairChoiceFormalError(f"{route_id} runtime route binding drifted")
        response_contracts[route_id] = contract
        canonical_generators[route_id] = generator

    return FairChoiceSciencePreflight(
        plan=dict(plan),
        public_manifest=dict(public_manifest),
        canary_plan=dict(canary_plan),
        generators=canonical_generators,
        response_contracts=response_contracts,
    )


_TRACE_RESPONSE_FIELDS = {
    "candidate_expression",
    "candidate_format",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "accepted_provider_request_count",
    "seed_supported",
    "provider_model",
    "finish_reason",
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "reasoning_tokens",
    "provider_fingerprint_sha256",
}


def _trace_response(response: GenerationResponse) -> dict[str, Any]:
    return {
        "candidate_expression": response.expression,
        "candidate_format": response.candidate_format,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "latency_ms": float(response.latency_ms),
        "accepted_provider_request_count": response.provider_request_count,
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


def _postcheck_public_inputs(
    state: FairChoiceSciencePreflight, public_manifest_path: str | Path
) -> None:
    if source_manifest(PROJECT_ROOT)["source_manifest_sha256"] != state.plan[
        "current_source_manifest_sha256"
    ]:
        raise FairChoiceFormalError("source changed during science execution")
    if benchmark.fair_config_file_sha256() != state.plan["file_bindings"][
        "fair_config"
    ]["file_sha256"]:
        raise FairChoiceFormalError("config changed during science execution")
    observed, _raw = _read_bound_json(
        public_manifest_path,
        state.plan["file_bindings"]["public_manifest"]["file_sha256"],
        label="public manifest postcheck",
    )
    benchmark.validate_public_manifest(observed)
    if observed != state.public_manifest:
        raise FairChoiceFormalError("public manifest changed during science execution")


def run_fair_choice_science(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    public_manifest_path: str | Path,
    canary_plan_path: str | Path,
    canary_artifact_paths: Mapping[str, str | Path],
    generators: Mapping[str, OpenAICompatibleGenerator],
) -> dict[str, Any]:
    """Run the frozen 192-call schedule and return only a complete joint bundle."""

    state = preflight_fair_choice_science(
        plan_path=plan_path,
        expected_plan_file_sha256=expected_plan_file_sha256,
        public_manifest_path=public_manifest_path,
        canary_plan_path=canary_plan_path,
        canary_artifact_paths=canary_artifact_paths,
        generators=generators,
    )
    tasks = {
        task["task_id"]: task for task in state.public_manifest["tasks"]
    }
    responses: dict[str, dict[str, GenerationResponse]] = {
        route_id: {} for route_id in CANONICAL_ROUTE_IDS
    }
    trace: list[dict[str, Any]] = []
    for scheduled in state.plan["execution"]["schedule"]:
        task = tasks[scheduled["task_id"]]
        route_id = scheduled["route_id"]
        response = state.generators[route_id].generate(
            task["rendered_prompt"],
            temperature=SCIENCE_TEMPERATURE,
            max_output_tokens=SCIENCE_MAX_OUTPUT_TOKENS,
            round_index=0,
            candidate_index=scheduled["task_ordinal"],
        )
        if type(response) is not GenerationResponse:
            raise FairChoiceFormalError(
                "science adapter returned a non-canonical GenerationResponse"
            )
        try:
            state.response_contracts[route_id].validate(response)
        except V3ResponseContractError as exc:
            raise FairChoiceFormalError(
                f"{route_id} response violated its canary contract"
            ) from exc
        responses[route_id][task["task_id"]] = response
        trace.append(
            {
                **dict(scheduled),
                "prompt_sha256": task["prompt_sha256"],
                "response": _trace_response(response),
            }
        )

    _postcheck_public_inputs(state, public_manifest_path)
    route_artifacts = [
        benchmark.build_response_artifact(
            state.public_manifest,
            responses[route_id],
            route_id=route_id,
        )
        for route_id in CANONICAL_ROUTE_IDS
    ]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": GENERATION_BUNDLE_KIND,
        "protocol_id": PROTOCOL_ID,
        "complete": True,
        "formal_plan_sha256": state.plan["formal_plan_sha256"],
        "formal_plan_file_sha256": expected_plan_file_sha256,
        "public_manifest_sha256": state.public_manifest["public_manifest_sha256"],
        "public_manifest_file_sha256": state.plan["file_bindings"][
            "public_manifest"
        ]["file_sha256"],
        "current_source_manifest_sha256": state.plan[
            "current_source_manifest_sha256"
        ],
        "fair_config_file_sha256": state.plan["file_bindings"]["fair_config"][
            "file_sha256"
        ],
        "schedule_sha256": state.plan["execution"]["schedule_sha256"],
        "route_ids": list(CANONICAL_ROUTE_IDS),
        "route_count": len(CANONICAL_ROUTE_IDS),
        "task_count_per_route": TASK_COUNT,
        "call_count": len(trace),
        "transport_failure_count": 0,
        "retry_or_resume": False,
        "route_response_artifacts": route_artifacts,
        "execution_trace": trace,
        "execution_trace_sha256": _sha256_json(trace),
    }
    bundle = {**unsigned, "generation_bundle_sha256": _sha256_json(unsigned)}
    validate_fair_choice_generation_bundle(state.plan, state.public_manifest, bundle)
    return bundle


def validate_fair_choice_generation_bundle(
    plan: Mapping[str, Any],
    public_manifest: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> None:
    """Validate a complete 192-call bundle without opening the private key."""

    validate_fair_choice_formal_plan(plan)
    benchmark.validate_public_manifest(public_manifest)
    expected_fields = {
        "schema_version",
        "kind",
        "protocol_id",
        "complete",
        "formal_plan_sha256",
        "formal_plan_file_sha256",
        "public_manifest_sha256",
        "public_manifest_file_sha256",
        "current_source_manifest_sha256",
        "fair_config_file_sha256",
        "schedule_sha256",
        "route_ids",
        "route_count",
        "task_count_per_route",
        "call_count",
        "transport_failure_count",
        "retry_or_resume",
        "route_response_artifacts",
        "execution_trace",
        "execution_trace_sha256",
        "generation_bundle_sha256",
    }
    if not isinstance(bundle, Mapping) or set(bundle) != expected_fields:
        raise FairChoiceFormalError("generation bundle uses a non-canonical schema")
    unsigned = {
        key: value for key, value in bundle.items() if key != "generation_bundle_sha256"
    }
    if (
        bundle.get("schema_version") != SCHEMA_VERSION
        or bundle.get("kind") != GENERATION_BUNDLE_KIND
        or bundle.get("protocol_id") != PROTOCOL_ID
        or bundle.get("complete") is not True
        or bundle.get("formal_plan_sha256") != plan["formal_plan_sha256"]
        or not _is_sha256(bundle.get("formal_plan_file_sha256"))
        or bundle.get("public_manifest_sha256")
        != public_manifest["public_manifest_sha256"]
        or bundle.get("public_manifest_file_sha256")
        != plan["file_bindings"]["public_manifest"]["file_sha256"]
        or bundle.get("current_source_manifest_sha256")
        != plan["current_source_manifest_sha256"]
        or bundle.get("fair_config_file_sha256")
        != plan["file_bindings"]["fair_config"]["file_sha256"]
        or bundle.get("schedule_sha256") != plan["execution"]["schedule_sha256"]
        or bundle.get("route_ids") != list(CANONICAL_ROUTE_IDS)
        or bundle.get("route_count") != len(CANONICAL_ROUTE_IDS)
        or bundle.get("task_count_per_route") != TASK_COUNT
        or bundle.get("call_count") != SCIENCE_TOTAL_CALLS
        or bundle.get("transport_failure_count") != 0
        or bundle.get("retry_or_resume") is not False
        or bundle.get("generation_bundle_sha256") != _sha256_json(unsigned)
    ):
        raise FairChoiceFormalError("generation bundle identity is malformed")

    artifacts = bundle["route_response_artifacts"]
    if (
        not isinstance(artifacts, list)
        or [artifact.get("route_id") for artifact in artifacts]
        != list(CANONICAL_ROUTE_IDS)
    ):
        raise FairChoiceFormalError("generation bundle route artifacts are incomplete")
    artifacts_by_route = {artifact["route_id"]: artifact for artifact in artifacts}
    for artifact in artifacts:
        benchmark.validate_response_artifact(artifact, public_manifest)

    trace = bundle["execution_trace"]
    if (
        not isinstance(trace, list)
        or len(trace) != SCIENCE_TOTAL_CALLS
        or bundle["execution_trace_sha256"] != _sha256_json(trace)
    ):
        raise FairChoiceFormalError("generation execution trace is incomplete")
    tasks = {task["task_id"]: task for task in public_manifest["tasks"]}
    qualifications = _qualification_by_route(plan)
    artifact_rows = {
        route_id: {
            row["task_id"]: row
            for row in artifacts_by_route[route_id]["tasks"]
        }
        for route_id in CANONICAL_ROUTE_IDS
    }
    trace_fields = {
        "call_index",
        "task_ordinal",
        "task_id",
        "route_id",
        "route_position",
        "prompt_sha256",
        "response",
    }
    for expected, row in zip(plan["execution"]["schedule"], trace, strict=True):
        if not isinstance(row, Mapping) or set(row) != trace_fields:
            raise FairChoiceFormalError("generation trace row fields are malformed")
        for field in (
            "call_index",
            "task_ordinal",
            "task_id",
            "route_id",
            "route_position",
        ):
            if row[field] != expected[field]:
                raise FairChoiceFormalError("generation trace differs from schedule")
        task = tasks.get(row["task_id"])
        if task is None or row["prompt_sha256"] != task["prompt_sha256"]:
            raise FairChoiceFormalError("generation trace prompt binding drifted")
        response = row["response"]
        if not isinstance(response, Mapping) or set(response) != _TRACE_RESPONSE_FIELDS:
            raise FairChoiceFormalError(
                "generation trace response fields are malformed"
            )
        contract = _accepted_response_contract(
            qualifications[row["route_id"]]["accepted_response_contract"]
        )
        try:
            contract.validate_checkpoint_payload(response)
        except (KeyError, TypeError, ValueError, V3ResponseContractError) as exc:
            raise FairChoiceFormalError(
                "generation trace response violates route contract"
            ) from exc
        artifact_row = artifact_rows[row["route_id"]][row["task_id"]]
        if (
            artifact_row["prompt_sha256"] != row["prompt_sha256"]
            or artifact_row["candidate_format"] != response["candidate_format"]
            or artifact_row["expression"] != response["candidate_expression"]
        ):
            raise FairChoiceFormalError("generation trace and route artifact differ")


def _load_analysis_public_context(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    public_manifest_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan, _raw = _read_bound_json(
        plan_path, expected_plan_file_sha256, label="formal plan"
    )
    validate_fair_choice_formal_plan(plan)
    if source_manifest(PROJECT_ROOT)["source_manifest_sha256"] != plan[
        "current_source_manifest_sha256"
    ]:
        raise FairChoiceFormalError("analysis source differs from formal plan")
    if benchmark.fair_config_file_sha256() != plan["file_bindings"]["fair_config"][
        "file_sha256"
    ]:
        raise FairChoiceFormalError("analysis config differs from formal plan")
    public, _public_raw = _read_bound_json(
        public_manifest_path,
        plan["file_bindings"]["public_manifest"]["file_sha256"],
        label="public manifest",
    )
    benchmark.validate_public_manifest(public)
    if public["public_manifest_sha256"] != plan["file_bindings"][
        "public_manifest"
    ]["inner_sha256"]:
        raise FairChoiceFormalError("analysis public manifest identity drifted")
    return plan, public


def _json_safe(value: object) -> Any:
    if isinstance(value, Fraction):
        return {
            "numerator": value.numerator,
            "denominator": value.denominator,
            "value": float(value),
        }
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def analyze_fair_choice_science(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    public_manifest_path: str | Path,
    generation_bundle_path: str | Path,
    expected_generation_bundle_file_sha256: str,
    private_key_path: str | Path,
) -> dict[str, Any]:
    """Validate the complete public bundle, then and only then open/score private."""

    plan, public_manifest = _load_analysis_public_context(
        plan_path=plan_path,
        expected_plan_file_sha256=expected_plan_file_sha256,
        public_manifest_path=public_manifest_path,
    )
    bundle, _bundle_raw = _read_bound_json(
        generation_bundle_path,
        expected_generation_bundle_file_sha256,
        label="generation bundle",
    )
    validate_fair_choice_generation_bundle(plan, public_manifest, bundle)
    if bundle["formal_plan_file_sha256"] != expected_plan_file_sha256:
        raise FairChoiceFormalError(
            "generation bundle names a different formal plan file"
        )

    # This is deliberately the first private-key read in the analysis path.
    private_key, _private_raw = _read_bound_json(
        private_key_path,
        plan["file_bindings"]["private_key"]["file_sha256"],
        label="private key",
    )
    benchmark.validate_private_key(private_key, public_manifest)
    if private_key["private_key_sha256"] != plan["file_bindings"]["private_key"][
        "inner_sha256"
    ]:
        raise FairChoiceFormalError("private key identity differs from formal plan")
    if (
        _public_private_bijection_sha256(public_manifest, private_key)
        != plan["benchmark_binding"]["public_private_task_bijection_sha256"]
    ):
        raise FairChoiceFormalError("public/private 64-task bijection drifted")

    artifacts_by_route = {
        artifact["route_id"]: artifact
        for artifact in bundle["route_response_artifacts"]
    }
    scores = {
        route_id: benchmark.score_model_responses(
            public_manifest,
            private_key,
            artifacts_by_route[route_id],
            model_id=route_id,
        )
        for route_id in CANONICAL_ROUTE_IDS
    }
    joint = _json_safe(benchmark.classify_joint_routes(scores))
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": JOINT_ANALYSIS_KIND,
        "protocol_id": PROTOCOL_ID,
        "formal_plan_sha256": plan["formal_plan_sha256"],
        "formal_plan_file_sha256": expected_plan_file_sha256,
        "generation_bundle_sha256": bundle["generation_bundle_sha256"],
        "generation_bundle_file_sha256": expected_generation_bundle_file_sha256,
        "public_manifest_sha256": public_manifest["public_manifest_sha256"],
        "private_key_sha256": private_key["private_key_sha256"],
        "current_source_manifest_sha256": plan["current_source_manifest_sha256"],
        "route_scores": scores,
        "joint_result": joint,
        "private_key_loaded_after_complete_generation_barrier": True,
    }
    return {**unsigned, "joint_analysis_sha256": _sha256_json(unsigned)}


def _parse_route_paths(values: Sequence[str], *, label: str) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise FairChoiceFormalError(f"{label} must use ROUTE=VALUE")
        route_id, raw_path = value.split("=", 1)
        if route_id not in CANONICAL_ROUTE_IDS or not raw_path or route_id in parsed:
            raise FairChoiceFormalError(f"{label} route mapping is malformed")
        parsed[route_id] = Path(raw_path)
    if set(parsed) != set(CANONICAL_ROUTE_IDS):
        raise FairChoiceFormalError(f"{label} requires all three canonical routes")
    return parsed


def _add_credential_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--deepseek-env-file", type=Path)
    parser.add_argument("--glm-env-file", type=Path)
    parser.add_argument("--deepseek-env-prefix", default="DEEPSEEK")
    parser.add_argument("--glm-env-prefix", default="TENCENT")


def _load_cli_generators(
    args: argparse.Namespace, plan: Mapping[str, Any]
) -> dict[str, OpenAICompatibleGenerator]:
    aliases = plan["execution"]["request_model_aliases"]
    deepseek_environment = dict(os.environ)
    deepseek_environment[
        f"{args.deepseek_env_prefix}_MODEL"
    ] = aliases["deepseek-flash"]
    deepseek = load_provider_credentials(
        prefix=args.deepseek_env_prefix,
        env_file=args.deepseek_env_file,
        environ=deepseek_environment,
    )
    glm_environment = dict(os.environ)
    glm_environment[f"{args.glm_env_prefix}_MODEL"] = aliases["glm-5.2"]
    glm = load_provider_credentials(
        prefix=args.glm_env_prefix,
        env_file=args.glm_env_file,
        environ=glm_environment,
    )
    # The account files may retain retired model names.  Only endpoint/key are
    # reused; the sealed plan supplies all aliases and preflight checks them.
    credentials = {
        "deepseek-flash": ProviderCredentials(
            base_url=deepseek.base_url,
            model=aliases["deepseek-flash"],
            api_key=deepseek.api_key,
        ),
        "deepseek-pro": ProviderCredentials(
            base_url=deepseek.base_url,
            model=aliases["deepseek-pro"],
            api_key=deepseek.api_key,
        ),
        "glm-5.2": ProviderCredentials(
            base_url=glm.base_url,
            model=aliases["glm-5.2"],
            api_key=glm.api_key,
        ),
    }
    return {
        route_id: build_v3_generator(credentials[route_id])
        for route_id in CANONICAL_ROUTE_IDS
    }


def _print_identity(path: Path, file_sha256: str) -> None:
    print(
        json.dumps(
            {"output": str(path), "file_sha256": file_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Formal strong-K4 fair-choice science coordinator"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--output-dir", type=Path, required=True)
    materialize_parser.add_argument(
        "--sealed-result", type=Path, default=benchmark.SEALED_SCAN_PATH
    )
    materialize_parser.add_argument("--canary-plan", type=Path, required=True)
    materialize_parser.add_argument(
        "--canary-artifact", action="append", default=[], metavar="ROUTE=PATH"
    )

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--plan", type=Path, required=True)
    run_parser.add_argument("--expected-plan-file-sha256", required=True)
    run_parser.add_argument("--public", type=Path, required=True)
    run_parser.add_argument("--canary-plan", type=Path, required=True)
    run_parser.add_argument(
        "--canary-artifact", action="append", default=[], metavar="ROUTE=PATH"
    )
    _add_credential_arguments(run_parser)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--execute", action="store_true")

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--plan", type=Path, required=True)
    analyze_parser.add_argument("--expected-plan-file-sha256", required=True)
    analyze_parser.add_argument("--public", type=Path, required=True)
    analyze_parser.add_argument("--bundle", type=Path, required=True)
    analyze_parser.add_argument("--expected-bundle-file-sha256", required=True)
    analyze_parser.add_argument("--private", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "materialize":
        result = materialize_fair_choice_formal(
            output_dir=args.output_dir,
            sealed_result_path=args.sealed_result,
            canary_plan_path=args.canary_plan,
            canary_artifact_paths=_parse_route_paths(
                args.canary_artifact, label="canary artifact"
            ),
        )
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    if args.command == "run":
        if not args.execute:
            parser.error("run requires --execute before any provider request")
        if os.path.lexists(args.output):
            raise FairChoiceFormalError(
                f"refusing to overwrite formal file {args.output}"
            )
        canary_paths = _parse_route_paths(
            args.canary_artifact, label="canary artifact"
        )
        plan, _raw = _read_bound_json(
            args.plan,
            args.expected_plan_file_sha256,
            label="formal plan",
        )
        validate_fair_choice_formal_plan(plan)
        generators = _load_cli_generators(args, plan)
        bundle = run_fair_choice_science(
            plan_path=args.plan,
            expected_plan_file_sha256=args.expected_plan_file_sha256,
            public_manifest_path=args.public,
            canary_plan_path=args.canary_plan,
            canary_artifact_paths=canary_paths,
            generators=generators,
        )
        file_sha256 = write_fair_choice_formal_json_exclusive(bundle, args.output)
        _print_identity(args.output, file_sha256)
        return 0
    if args.command == "analyze":
        if os.path.lexists(args.output):
            raise FairChoiceFormalError(
                f"refusing to overwrite formal file {args.output}"
            )
        analysis = analyze_fair_choice_science(
            plan_path=args.plan,
            expected_plan_file_sha256=args.expected_plan_file_sha256,
            public_manifest_path=args.public,
            generation_bundle_path=args.bundle,
            expected_generation_bundle_file_sha256=(
                args.expected_bundle_file_sha256
            ),
            private_key_path=args.private,
        )
        file_sha256 = write_fair_choice_formal_json_exclusive(analysis, args.output)
        _print_identity(args.output, file_sha256)
        return 0
    raise AssertionError("unreachable formal command")


__all__ = [
    "FairChoiceFormalError",
    "FairChoiceSciencePreflight",
    "FORMAL_PLAN_KIND",
    "GENERATION_BUNDLE_KIND",
    "JOINT_ANALYSIS_KIND",
    "ROUTE_PERMUTATION_MULTIPLICITIES",
    "analyze_fair_choice_science",
    "build_balanced_fair_choice_schedule",
    "build_fair_choice_formal_plan",
    "fair_choice_formal_file_sha256",
    "main",
    "materialize_fair_choice_formal",
    "preflight_fair_choice_science",
    "run_fair_choice_science",
    "validate_fair_choice_formal_plan",
    "validate_fair_choice_generation_bundle",
    "write_fair_choice_formal_json_exclusive",
]


if __name__ == "__main__":
    raise SystemExit(main())
