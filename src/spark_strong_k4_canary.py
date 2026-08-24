"""Target-free route canary for the strong-K4 fair-choice benchmark.

The canary exercises only the provider adapter, the strict JSON-expression
parser, and the opaque-option prompt format.  Its retired world is constructed
without drawing a target, and this module never computes K1--K4 or invokes a
compressor.  Network access occurs only when :func:`run_fair_choice_canary` is
called with a concrete ``OpenAICompatibleGenerator``.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import dsl, spark_closure, spark_cross_model, spark_lineage
from .credentials import load_provider_credentials
from .provenance import PROJECT_ROOT, source_manifest
from .providers.openai_compatible import OpenAICompatibleGenerator
from .runner import CANDIDATE_FORMATS, GenerationResponse
from .spark_strong_k4_benchmark import (
    CANONICAL_ROUTE_IDS,
    PROTOCOL_ID as FAIR_CHOICE_PROTOCOL_ID,
    action_order_for_pair,
    fair_config_file_sha256,
    option_ids_for_pair,
    render_fair_choice_prompt,
)
from . import spark_strong_k4_benchmark as fair_benchmark
from . import staged_pilot_v3 as staged_v3
from .staged_pilot_v3 import (
    AcceptedResponseContract,
    V3ResponseContractError,
    route_binding_sha256,
)
from .v3_live import build_v3_generator


SCHEMA_VERSION = 1
FAIR_CHOICE_CANARY_ID = "spark-strong-k4-fair-choice-opaque-canary-v1"
FAIR_CHOICE_CANARY_WORLD_SEED = 1000
FAIR_CHOICE_CANARY_CALLS_PER_ROUTE = 4
FAIR_CHOICE_CANARY_TEMPERATURE = 0.2
FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS = 256
FAIR_CHOICE_CANARY_THINKING = "disabled"
FAIR_CHOICE_CANARY_MOTIF_NAMESPACE = FAIR_CHOICE_CANARY_ID

FAIR_CHOICE_CANARY_PLAN_KIND = "spark-strong-k4-fair-choice-canary-plan"
FAIR_CHOICE_CANARY_ARTIFACT_KIND = "spark-strong-k4-fair-choice-canary-artifact"
FAIR_CHOICE_CANARY_EVIDENCE_SCOPE = (
    "target_free_opaque_format_and_current_route_contract_calibration_only"
)


class FairChoiceCanaryError(ValueError):
    """A fair-choice canary plan, response, or artifact is malformed."""


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
        raise FairChoiceCanaryError("canary values must be canonical JSON") from exc


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_clone(value: object) -> Any:
    return json.loads(_canonical_json_bytes(value).decode("utf-8"))


def _route_expectations() -> list[dict[str, Any]]:
    """Copy the prior audited route contracts as provenance and expectations."""

    if tuple(CANONICAL_ROUTE_IDS) != tuple(spark_cross_model.CROSS_MODEL_ARM_IDS):
        raise FairChoiceCanaryError("fair-choice and cross-model route order drifted")
    expectations: list[dict[str, Any]] = []
    for route_id in CANONICAL_ROUTE_IDS:
        frozen = spark_cross_model._ROUTE_FREEZES[route_id]
        prior_contract = spark_cross_model._accepted_response_contract(
            frozen["accepted_response_contract"]
        ).to_dict()
        expectations.append(
            {
                "route_id": route_id,
                "model_stratum": frozen["model_stratum"],
                "provider_profile": frozen["provider_profile"],
                "request_model": frozen["request_model"],
                "response_model": frozen["response_model"],
                "sanitized_request_contract": _json_clone(
                    frozen["sanitized_request_contract"]
                ),
                "prior_action_canary_artifact_sha256": frozen[
                    "canary_artifact_sha256"
                ],
                "prior_action_canary_plan_sha256": frozen["canary_plan_sha256"],
                "prior_action_canary_prompt_set_sha256": (
                    spark_cross_model.ACTION_CANARY_PROMPT_SET_SHA256
                ),
                "prior_accepted_response_contract": prior_contract,
                "prior_route_binding_sha256": frozen["route_binding_sha256"],
            }
        )
    return expectations


def _assert_frozen_config_route_contract(
    route_expectations: Sequence[Mapping[str, Any]],
) -> None:
    """Keep canary request parameters tied to the byte-sealed preregistration."""

    config = fair_benchmark._load_frozen_fair_config()
    planned = config.get("planned_routes")
    canary = config.get("new_format_canary")
    barrier = config.get("later_sealed_plan_barrier")
    if not all(isinstance(value, Mapping) for value in (planned, canary, barrier)):
        raise FairChoiceCanaryError("frozen fair config canary sections are malformed")
    assert isinstance(planned, Mapping)
    assert isinstance(canary, Mapping)
    assert isinstance(barrier, Mapping)
    route_ids = list(CANONICAL_ROUTE_IDS)
    aliases = {
        route["route_id"]: route["request_model"] for route in route_expectations
    }
    if (
        planned.get("route_ids") != route_ids
        or planned.get("request_model_aliases") != aliases
        or planned.get("route_count") != len(route_ids)
        or planned.get("temperature") != FAIR_CHOICE_CANARY_TEMPERATURE
        or planned.get("max_output_tokens")
        != FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS
        or planned.get("thinking") != FAIR_CHOICE_CANARY_THINKING
    ):
        raise FairChoiceCanaryError(
            "canary route or request parameters differ from frozen fair config"
        )
    if (
        canary.get("required_before_science_calls") is not True
        or canary.get("uses_only_retired_target_free_worlds") is not True
        or canary.get("old_action_grammar_canary_is_sufficient") is not False
        or canary.get("knowledge_endpoints_may_be_read") is not False
        or barrier.get("provider_message_may_contain")
        != (
            "rendered_prompt bytes only; do not send task_id or provider metadata "
            "derived from pair/arm"
        )
    ):
        raise FairChoiceCanaryError("frozen fair config canary boundary drifted")


def _prompt_task(
    public_world: Mapping[str, Any],
    *,
    serial_index: int,
    motif_stratum: str,
) -> dict[str, Any]:
    slot_index = serial_index + 1
    motif, selection_sha256 = spark_closure._select_motif(
        FAIR_CHOICE_CANARY_WORLD_SEED,
        slot_index,
        motif_stratum,
        namespace=FAIR_CHOICE_CANARY_MOTIF_NAMESPACE,
    )
    public_world_sha256 = _sha256_json(public_world)
    pair_anchor_sha256 = _sha256_json(
        {
            "canary_id": FAIR_CHOICE_CANARY_ID,
            "public_world_sha256": public_world_sha256,
            "slot_index": slot_index,
            "motif_stratum": motif_stratum,
            "motif_selection_sha256": selection_sha256,
        }
    )
    action_order = action_order_for_pair(serial_index)
    option_ids = option_ids_for_pair(pair_anchor_sha256)
    public_context = fair_benchmark._public_context_from_world_entry(public_world)
    motif_sexpr = dsl.to_sexpr(motif.ast)
    prompt = render_fair_choice_prompt(
        public_context,
        motif_sexpr,
        action_order,
        option_ids,
    )
    task_identity = _sha256_json(
        {
            "canary_id": FAIR_CHOICE_CANARY_ID,
            "public_world_sha256": public_world_sha256,
            "slot_index": slot_index,
            "motif_selection_sha256": selection_sha256,
            "prompt_sha256": _sha256_text(prompt),
        }
    )
    return {
        "serial_index": serial_index,
        "task_id": f"CANARY-{task_identity[:16].upper()}",
        "world_index": 0,
        "world_seed": FAIR_CHOICE_CANARY_WORLD_SEED,
        "slot_index": slot_index,
        "motif_id": motif.motif_id,
        "motif_stratum": motif.stratum,
        "motif_sexpr": motif_sexpr,
        "motif_selection_sha256": selection_sha256,
        "action_order": list(action_order),
        "opaque_option_ids": list(option_ids),
        "rendered_prompt": prompt,
        "prompt_sha256": _sha256_text(prompt),
    }


def build_fair_choice_canary_plan() -> dict[str, Any]:
    """Build the fixed four-prompt target-free canary plan.

    The sole world seed is retired from formal evidence.  Construction touches
    only the conditioned public bank, D0, target-free parent, motif library, and
    prompt renderer.  Hidden targets and all knowledge endpoints remain absent.
    """

    public_world = spark_closure._target_free_public_world_entry(
        0, FAIR_CHOICE_CANARY_WORLD_SEED
    )
    tasks = [
        _prompt_task(
            public_world,
            serial_index=serial_index,
            motif_stratum=motif_stratum,
        )
        for serial_index, motif_stratum in enumerate(spark_lineage.MOTIF_STRATA)
    ]
    prompt_schedule = [
        {
            "serial_index": task["serial_index"],
            "task_id": task["task_id"],
            "prompt_sha256": task["prompt_sha256"],
            "opaque_option_ids": task["opaque_option_ids"],
        }
        for task in tasks
    ]
    source_sha256 = source_manifest(PROJECT_ROOT)["source_manifest_sha256"]
    config_sha256 = fair_config_file_sha256()
    route_expectations = _route_expectations()
    _assert_frozen_config_route_contract(route_expectations)
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": FAIR_CHOICE_CANARY_PLAN_KIND,
        "canary_id": FAIR_CHOICE_CANARY_ID,
        "benchmark_protocol_id": FAIR_CHOICE_PROTOCOL_ID,
        "evidence": False,
        "evidence_scope": FAIR_CHOICE_CANARY_EVIDENCE_SCOPE,
        "current_source_manifest_sha256": source_sha256,
        "fair_config_file_sha256": config_sha256,
        "world_seed": FAIR_CHOICE_CANARY_WORLD_SEED,
        "public_world": public_world,
        "public_world_sha256": _sha256_json(public_world),
        "route_expectations": route_expectations,
        "tasks": tasks,
        "prompt_set_sha256": _sha256_json(prompt_schedule),
        "protocol": {
            "world_is_retired_from_formal_evidence": True,
            "world_count": 1,
            "motif_strata": list(spark_lineage.MOTIF_STRATA),
            "one_prompt_per_motif_stratum": True,
            "logical_calls_per_route": FAIR_CHOICE_CANARY_CALLS_PER_ROUTE,
            "route_count": len(CANONICAL_ROUTE_IDS),
            "total_logical_calls": (
                FAIR_CHOICE_CANARY_CALLS_PER_ROUTE * len(CANONICAL_ROUTE_IDS)
            ),
            "temperature": FAIR_CHOICE_CANARY_TEMPERATURE,
            "max_output_tokens": FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS,
            "thinking": FAIR_CHOICE_CANARY_THINKING,
            "physical_attempts_per_task": 1,
            "retry_performed": False,
            "hidden_target_derived": False,
            "K1_K2_K3_K4_evaluated": False,
            "compressor_run": False,
            "old_action_canary_sufficient": False,
            "provider_message": "rendered_prompt_only",
            "content_rule": (
                "valid_iff_json_expression_exactly_matches_one_task_opaque_id"
            ),
            "invalid_content_completes_without_retry": True,
        },
    }
    return {**unsigned, "canary_plan_sha256": _sha256_json(unsigned)}


def validate_fair_choice_canary_plan(plan: Mapping[str, Any]) -> None:
    """Verify the exact current-source canary design and every prompt digest."""

    if not isinstance(plan, Mapping):
        raise FairChoiceCanaryError("fair-choice canary plan must be an object")
    expected = build_fair_choice_canary_plan()
    if dict(plan) != expected:
        raise FairChoiceCanaryError("fair-choice canary plan differs from fixed design")


def _route_expectation(
    plan: Mapping[str, Any], route_id: str
) -> Mapping[str, Any]:
    if route_id not in CANONICAL_ROUTE_IDS:
        raise FairChoiceCanaryError("canary route is not one of the canonical routes")
    routes = plan.get("route_expectations")
    if not isinstance(routes, list):
        raise FairChoiceCanaryError("canary route expectations are malformed")
    matches = [route for route in routes if route.get("route_id") == route_id]
    if len(matches) != 1:
        raise FairChoiceCanaryError("canary route id is absent or non-unique")
    return matches[0]


def preflight_fair_choice_route(
    plan: Mapping[str, Any],
    route_id: str,
    generator: OpenAICompatibleGenerator,
) -> dict[str, Any]:
    """Bind the concrete one-shot adapter to the frozen request route.

    The prior accepted-response contract and binding are provenance only.  A
    fresh response contract and route binding are calibrated from this canary's
    four completed calls, so they are deliberately not required here.
    """

    validate_fair_choice_canary_plan(plan)
    route = _route_expectation(plan, route_id)
    if type(generator) is not OpenAICompatibleGenerator:
        raise FairChoiceCanaryError(
            "canary execution requires an exact OpenAICompatibleGenerator"
        )
    if generator.model != route["request_model"]:
        raise FairChoiceCanaryError("runtime request model differs from canary route")
    if generator.sanitized_request_contract() != route[
        "sanitized_request_contract"
    ]:
        raise FairChoiceCanaryError(
            "runtime sanitized request contract differs from canary route"
        )
    return dict(route)


def _derive_current_response_contract(
    generator: OpenAICompatibleGenerator,
    responses: Sequence[GenerationResponse],
    *,
    expected_response_model: str,
) -> AcceptedResponseContract:
    if len(responses) != FAIR_CHOICE_CANARY_CALLS_PER_ROUTE:
        raise FairChoiceCanaryError("canary response count is incomplete")
    if any(type(response) is not GenerationResponse for response in responses):
        raise FairChoiceCanaryError("canary adapter returned a non-canonical response")
    if any(response.provider_request_count != 1 for response in responses):
        raise FairChoiceCanaryError("canary made more than one request for a task")
    if any(response.provider_model != expected_response_model for response in responses):
        raise FairChoiceCanaryError("canary response model differs from its route")
    if any(response.finish_reason not in {"stop", "length"} for response in responses):
        raise FairChoiceCanaryError("canary finish reason is unsupported")
    if any(
        response.output_tokens > FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS
        for response in responses
    ):
        raise FairChoiceCanaryError("canary response exceeded the output cap")
    if any(response.seed_supported is not generator.seed_supported for response in responses):
        raise FairChoiceCanaryError("canary seed capability is inconsistent")
    if any(response.reasoning_tokens not in {None, 0} for response in responses):
        raise FairChoiceCanaryError("canary did not keep reasoning disabled")
    if any(response.candidate_format not in CANDIDATE_FORMATS for response in responses):
        raise FairChoiceCanaryError("canary candidate format is outside the closed set")

    cache_pairs = [
        (response.prompt_cache_hit_tokens, response.prompt_cache_miss_tokens)
        for response in responses
    ]
    if all(pair == (None, None) for pair in cache_pairs):
        cache_mode = "absent"
    elif all(
        type(hit) is int
        and type(miss) is int
        and response.input_tokens == hit + miss
        for response, (hit, miss) in zip(responses, cache_pairs, strict=True)
    ):
        cache_mode = "complete"
    else:
        raise FairChoiceCanaryError("canary cache telemetry is inconsistent")

    fingerprints = [response.provider_fingerprint for response in responses]
    if all(value is None for value in fingerprints):
        fingerprint_mode = "absent"
        fingerprint_sha256 = None
    elif all(
        isinstance(value, str) and value.strip() for value in fingerprints
    ) and len(set(fingerprints)) == 1:
        fingerprint_mode = "exact_sha256"
        fingerprint_sha256 = _sha256_text(str(fingerprints[0]))
    else:
        raise FairChoiceCanaryError("canary provider fingerprint is unstable")

    try:
        contract = AcceptedResponseContract(
            provider_models=(expected_response_model,),
            finish_reasons=("stop", "length"),
            max_output_tokens=FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS,
            seed_supported=generator.seed_supported,
            require_zero_reasoning_tokens=True,
            prompt_cache_mode=cache_mode,
            provider_fingerprint_mode=fingerprint_mode,
            provider_fingerprint_sha256=fingerprint_sha256,
        )
        for response in responses:
            contract.validate(response)
    except (TypeError, ValueError, V3ResponseContractError) as exc:
        raise FairChoiceCanaryError(
            "canary responses do not define one accepted response contract"
        ) from exc
    return contract


def _response_record(
    task: Mapping[str, Any], response: GenerationResponse
) -> dict[str, Any]:
    option_ids = task["opaque_option_ids"]
    valid = (
        response.candidate_format == "json_expression"
        and isinstance(response.expression, str)
        and response.expression in option_ids
    )
    if valid:
        selected_option_id = response.expression
        parse_status = "valid_opaque_option"
        invalid_reason = None
    elif response.candidate_format != "json_expression":
        selected_option_id = None
        parse_status = "invalid_opaque_option"
        invalid_reason = "candidate_format_not_json_expression"
    elif not isinstance(response.expression, str):
        selected_option_id = None
        parse_status = "invalid_opaque_option"
        invalid_reason = "expression_not_string"
    else:
        selected_option_id = None
        parse_status = "invalid_opaque_option"
        invalid_reason = "expression_not_listed_opaque_option"
    return {
        "serial_index": task["serial_index"],
        "task_id": task["task_id"],
        "motif_stratum": task["motif_stratum"],
        "prompt_sha256": task["prompt_sha256"],
        "opaque_choice_valid": valid,
        "selected_option_id": selected_option_id,
        "invalid_reason": invalid_reason,
        "response": {
            "candidate_expression": selected_option_id,
            "candidate_parse_status": parse_status,
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
                else _sha256_text(response.provider_fingerprint)
            ),
        },
    }


def _derived_route_binding(
    request_contract: Mapping[str, Any], response_contract: Mapping[str, Any]
) -> str:
    return _sha256_json(
        {
            "coordinator_version": staged_v3.V3_COORDINATOR_VERSION,
            "accepted_attempt_estimand": staged_v3.V3_ACCEPTED_ATTEMPT_ESTIMAND,
            "request_contract": dict(request_contract),
            "response_contract": dict(response_contract),
        }
    )


def run_fair_choice_canary(
    plan: Mapping[str, Any],
    route_id: str,
    generator: OpenAICompatibleGenerator,
) -> dict[str, Any]:
    """Run four one-shot opaque-choice calls and seal a complete artifact.

    Provider and transport exceptions propagate directly.  No partial artifact
    is returned, and malformed model content is a completed canary observation
    rather than a retry trigger.
    """

    route = preflight_fair_choice_route(plan, route_id, generator)
    responses: list[GenerationResponse] = []
    records: list[dict[str, Any]] = []
    for task in plan["tasks"]:
        response = generator.generate(
            task["rendered_prompt"],
            temperature=FAIR_CHOICE_CANARY_TEMPERATURE,
            max_output_tokens=FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS,
            round_index=0,
            candidate_index=int(task["serial_index"]),
        )
        if type(response) is not GenerationResponse:
            raise FairChoiceCanaryError(
                "canary adapter returned a non-canonical GenerationResponse"
            )
        responses.append(response)
        records.append(_response_record(task, response))

    contract = _derive_current_response_contract(
        generator,
        responses,
        expected_response_model=str(route["response_model"]),
    )
    valid_count = sum(record["opaque_choice_valid"] for record in records)
    candidate_format_counts = dict(
        sorted(Counter(response.candidate_format for response in responses).items())
    )
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": FAIR_CHOICE_CANARY_ARTIFACT_KIND,
        "canary_id": FAIR_CHOICE_CANARY_ID,
        "benchmark_protocol_id": FAIR_CHOICE_PROTOCOL_ID,
        "evidence": False,
        "evidence_scope": FAIR_CHOICE_CANARY_EVIDENCE_SCOPE,
        "route_id": route_id,
        "canary_plan_sha256": plan["canary_plan_sha256"],
        "prompt_set_sha256": plan["prompt_set_sha256"],
        "current_source_manifest_sha256": plan[
            "current_source_manifest_sha256"
        ],
        "fair_config_file_sha256": plan["fair_config_file_sha256"],
        "request_model": route["request_model"],
        "response_model": route["response_model"],
        "sanitized_request_contract": _json_clone(
            route["sanitized_request_contract"]
        ),
        "accepted_response_contract": contract.to_dict(),
        "route_binding_sha256": route_binding_sha256(generator, contract),
        "prior_action_canary_artifact_sha256": route[
            "prior_action_canary_artifact_sha256"
        ],
        "prior_route_binding_sha256": route["prior_route_binding_sha256"],
        "call_count": len(records),
        "transport_failure_count": 0,
        "retry_performed": False,
        "valid_choice_count": valid_count,
        "invalid_choice_count": len(records) - valid_count,
        "candidate_format_counts": candidate_format_counts,
        "passed": valid_count == FAIR_CHOICE_CANARY_CALLS_PER_ROUTE,
        "contract_satisfied": True,
        "knowledge_endpoint_read": False,
        "records": records,
    }
    artifact = {
        **unsigned,
        "canary_artifact_sha256": _sha256_json(unsigned),
    }
    validate_fair_choice_canary_artifact(plan, artifact)
    return artifact


def _contract_from_artifact(value: object) -> AcceptedResponseContract:
    if not isinstance(value, Mapping):
        raise FairChoiceCanaryError("accepted response contract must be an object")
    expected_fields = {
        "provider_models",
        "finish_reasons",
        "max_output_tokens",
        "seed_supported",
        "require_zero_reasoning_tokens",
        "prompt_cache_mode",
        "provider_fingerprint_mode",
        "provider_fingerprint_sha256",
    }
    if set(value) != expected_fields:
        raise FairChoiceCanaryError("accepted response contract fields drifted")
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
        raise FairChoiceCanaryError("accepted response contract is malformed") from exc
    if contract.to_dict() != dict(value):
        raise FairChoiceCanaryError("accepted response contract is not canonical")
    return contract


def validate_fair_choice_canary_artifact(
    plan: Mapping[str, Any], artifact: Mapping[str, Any]
) -> None:
    """Validate one complete canary artifact without reading any private key."""

    validate_fair_choice_canary_plan(plan)
    expected_fields = {
        "schema_version",
        "kind",
        "canary_id",
        "benchmark_protocol_id",
        "evidence",
        "evidence_scope",
        "route_id",
        "canary_plan_sha256",
        "prompt_set_sha256",
        "current_source_manifest_sha256",
        "fair_config_file_sha256",
        "request_model",
        "response_model",
        "sanitized_request_contract",
        "accepted_response_contract",
        "route_binding_sha256",
        "prior_action_canary_artifact_sha256",
        "prior_route_binding_sha256",
        "call_count",
        "transport_failure_count",
        "retry_performed",
        "valid_choice_count",
        "invalid_choice_count",
        "candidate_format_counts",
        "passed",
        "contract_satisfied",
        "knowledge_endpoint_read",
        "records",
        "canary_artifact_sha256",
    }
    if not isinstance(artifact, Mapping) or set(artifact) != expected_fields:
        raise FairChoiceCanaryError("fair-choice canary artifact fields drifted")
    unsigned = {
        key: value
        for key, value in artifact.items()
        if key != "canary_artifact_sha256"
    }
    if artifact["canary_artifact_sha256"] != _sha256_json(unsigned):
        raise FairChoiceCanaryError("fair-choice canary artifact digest mismatch")
    route = _route_expectation(plan, str(artifact.get("route_id")))
    fixed_identity = {
        "schema_version": SCHEMA_VERSION,
        "kind": FAIR_CHOICE_CANARY_ARTIFACT_KIND,
        "canary_id": FAIR_CHOICE_CANARY_ID,
        "benchmark_protocol_id": FAIR_CHOICE_PROTOCOL_ID,
        "evidence": False,
        "evidence_scope": FAIR_CHOICE_CANARY_EVIDENCE_SCOPE,
        "canary_plan_sha256": plan["canary_plan_sha256"],
        "prompt_set_sha256": plan["prompt_set_sha256"],
        "current_source_manifest_sha256": plan[
            "current_source_manifest_sha256"
        ],
        "fair_config_file_sha256": plan["fair_config_file_sha256"],
        "request_model": route["request_model"],
        "response_model": route["response_model"],
        "sanitized_request_contract": route["sanitized_request_contract"],
        "prior_action_canary_artifact_sha256": route[
            "prior_action_canary_artifact_sha256"
        ],
        "prior_route_binding_sha256": route["prior_route_binding_sha256"],
        "call_count": FAIR_CHOICE_CANARY_CALLS_PER_ROUTE,
        "transport_failure_count": 0,
        "retry_performed": False,
        "contract_satisfied": True,
        "knowledge_endpoint_read": False,
    }
    for field, expected in fixed_identity.items():
        if artifact.get(field) != expected:
            raise FairChoiceCanaryError(f"canary artifact {field} drifted")

    contract = _contract_from_artifact(artifact["accepted_response_contract"])
    if (
        contract.provider_models != (route["response_model"],)
        or contract.finish_reasons != ("stop", "length")
        or contract.max_output_tokens != FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS
        or contract.seed_supported is not False
        or not contract.require_zero_reasoning_tokens
    ):
        raise FairChoiceCanaryError("new accepted response contract is incompatible")
    expected_binding = _derived_route_binding(
        route["sanitized_request_contract"], contract.to_dict()
    )
    if artifact["route_binding_sha256"] != expected_binding:
        raise FairChoiceCanaryError("new route binding digest mismatch")

    records = artifact["records"]
    if not isinstance(records, list) or len(records) != len(plan["tasks"]):
        raise FairChoiceCanaryError("canary artifact must contain four records")
    record_fields = {
        "serial_index",
        "task_id",
        "motif_stratum",
        "prompt_sha256",
        "opaque_choice_valid",
        "selected_option_id",
        "invalid_reason",
        "response",
    }
    response_fields = {
        "candidate_expression",
        "candidate_parse_status",
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
    valid_count = 0
    format_counts: Counter[str] = Counter()
    for task, record in zip(plan["tasks"], records, strict=True):
        if not isinstance(record, Mapping) or set(record) != record_fields:
            raise FairChoiceCanaryError("canary record fields drifted")
        for field in ("serial_index", "task_id", "motif_stratum", "prompt_sha256"):
            if record[field] != task[field]:
                raise FairChoiceCanaryError("canary record task binding drifted")
        payload = record["response"]
        if not isinstance(payload, Mapping) or set(payload) != response_fields:
            raise FairChoiceCanaryError("canary response record fields drifted")
        try:
            contract.validate_checkpoint_payload(payload)
        except (TypeError, ValueError, KeyError, V3ResponseContractError) as exc:
            raise FairChoiceCanaryError(
                "canary record violates its accepted response contract"
            ) from exc
        candidate_format = payload["candidate_format"]
        format_counts[str(candidate_format)] += 1
        valid = record["opaque_choice_valid"]
        if type(valid) is not bool:
            raise FairChoiceCanaryError("opaque choice validity must be boolean")
        if valid:
            valid_count += 1
            selected = record["selected_option_id"]
            if (
                candidate_format != "json_expression"
                or selected not in task["opaque_option_ids"]
                or payload["candidate_expression"] != selected
                or payload["candidate_parse_status"] != "valid_opaque_option"
                or record["invalid_reason"] is not None
            ):
                raise FairChoiceCanaryError("valid opaque choice record is malformed")
        else:
            allowed_reasons = {
                "candidate_format_not_json_expression",
                "expression_not_string",
                "expression_not_listed_opaque_option",
            }
            if (
                record["selected_option_id"] is not None
                or payload["candidate_expression"] is not None
                or payload["candidate_parse_status"] != "invalid_opaque_option"
                or record["invalid_reason"] not in allowed_reasons
            ):
                raise FairChoiceCanaryError("invalid opaque choice record is malformed")
            if (
                candidate_format == "json_expression"
                and record["invalid_reason"] == "candidate_format_not_json_expression"
            ) or (
                candidate_format != "json_expression"
                and record["invalid_reason"] != "candidate_format_not_json_expression"
            ):
                raise FairChoiceCanaryError("invalid opaque choice reason drifted")

    if artifact["candidate_format_counts"] != dict(sorted(format_counts.items())):
        raise FairChoiceCanaryError("canary candidate format counts drifted")
    if (
        artifact["valid_choice_count"] != valid_count
        or artifact["invalid_choice_count"] != len(records) - valid_count
        or artifact["passed"]
        is not (valid_count == FAIR_CHOICE_CANARY_CALLS_PER_ROUTE)
    ):
        raise FairChoiceCanaryError("canary content pass counts drifted")


def fair_choice_canary_file_sha256(path: str | Path) -> str:
    """Return the exact byte identity of a persisted plan or canary artifact."""

    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise FairChoiceCanaryError(f"cannot read canary JSON file {source}") from exc
    return hashlib.sha256(payload).hexdigest()


def write_fair_choice_canary_json_exclusive(
    value: Mapping[str, Any], output: str | Path
) -> str:
    """Persist deterministic JSON once with mode 0600 and return its file hash."""

    if not isinstance(value, Mapping):
        raise FairChoiceCanaryError("canary JSON output must be an object")
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        rendered = (
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
        raise FairChoiceCanaryError("canary JSON output is not finite JSON") from exc
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise FairChoiceCanaryError(f"refusing to overwrite canary file {path}") from exc
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
        # Preserve an exclusive partial file as evidence of a failed write.
        raise
    return hashlib.sha256(rendered).hexdigest()


def _read_canary_plan(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FairChoiceCanaryError(f"cannot read canary plan {source}") from exc
    if not isinstance(value, dict):
        raise FairChoiceCanaryError("canary plan file must contain a JSON object")
    validate_fair_choice_canary_plan(value)
    return value


def _require_output_absent(path: str | Path) -> None:
    if os.path.lexists(Path(path)):
        raise FairChoiceCanaryError(f"refusing to overwrite canary file {path}")


def _print_file_identity(path: Path, file_sha256: str) -> None:
    print(
        json.dumps(
            {"output": str(path), "file_sha256": file_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Build a plan or explicitly execute one four-call route canary."""

    parser = argparse.ArgumentParser(
        description="Target-free strong-K4 fair-choice route canary"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--output", type=Path, required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--plan", type=Path, required=True)
    run_parser.add_argument("--route-id", choices=CANONICAL_ROUTE_IDS, required=True)
    run_parser.add_argument("--env-file", type=Path)
    run_parser.add_argument("--env-prefix", required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--execute", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "plan":
        _require_output_absent(args.output)
        plan = build_fair_choice_canary_plan()
        file_sha256 = write_fair_choice_canary_json_exclusive(plan, args.output)
        _print_file_identity(args.output, file_sha256)
        return 0
    if args.command == "run":
        if not args.execute:
            parser.error("run requires --execute before any provider request")
        _require_output_absent(args.output)
        plan = _read_canary_plan(args.plan)
        credentials = load_provider_credentials(
            prefix=args.env_prefix,
            env_file=args.env_file,
        )
        generator = build_v3_generator(credentials)
        artifact = run_fair_choice_canary(
            plan,
            args.route_id,
            generator,
        )
        file_sha256 = write_fair_choice_canary_json_exclusive(
            artifact, args.output
        )
        _print_file_identity(args.output, file_sha256)
        return 0
    raise AssertionError("unreachable canary command")


__all__ = [
    "FAIR_CHOICE_CANARY_ARTIFACT_KIND",
    "FAIR_CHOICE_CANARY_CALLS_PER_ROUTE",
    "FAIR_CHOICE_CANARY_ID",
    "FAIR_CHOICE_CANARY_MAX_OUTPUT_TOKENS",
    "FAIR_CHOICE_CANARY_PLAN_KIND",
    "FAIR_CHOICE_CANARY_TEMPERATURE",
    "FAIR_CHOICE_CANARY_THINKING",
    "FAIR_CHOICE_CANARY_WORLD_SEED",
    "FairChoiceCanaryError",
    "build_fair_choice_canary_plan",
    "fair_choice_canary_file_sha256",
    "main",
    "preflight_fair_choice_route",
    "run_fair_choice_canary",
    "validate_fair_choice_canary_artifact",
    "validate_fair_choice_canary_plan",
    "write_fair_choice_canary_json_exclusive",
]


if __name__ == "__main__":  # pragma: no cover - exercised through main tests
    raise SystemExit(main())
