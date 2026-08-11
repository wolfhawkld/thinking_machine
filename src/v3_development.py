"""Pure preflight and execution-plan builder for the v3 development study.

This module performs no network I/O and never reads credentials.  The checked-
in JSON is a template with deliberately null model identities; it cannot become
live-eligible until route-specific canaries bind those identities and a source
manifest hash.  Plan construction is deterministic and contains only public
world coordinates and hashes, never generated examples or hidden laws.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from .experiment import _arm_execution_order, validate_config
from .pilot_checkpoint import canonical_json_bytes, sha256_json
from .world_generator import generate_world


PROJECT_ROOT = Path(__file__).resolve().parents[1]
V3_TEMPLATE_PATH = PROJECT_ROOT / "configs" / "v3-development.template.json"
DEVELOPMENT_SEED_REGISTRY_PATH = (
    PROJECT_ROOT / "configs" / "development-seed-registry.json"
)
V3_MODEL_STRATA = ("official-deepseek-v4", "official-glm-5.2")
V3_ARM_IDS = ("L", "H", "C", "E2")
V3_CALLS_PER_SHARD = 20
V3_GATE_SHARDS = 8
V3_MAIN_SHARDS = 96
V3_TOTAL_SHARDS = V3_GATE_SHARDS + V3_MAIN_SHARDS
V3_ROUTE_CONTRACT_SCHEMA_VERSION = 1
V3_COORDINATOR_VERSION = "logical-slot-recovery-v1"
V3_ACCEPTED_ATTEMPT_ESTIMAND = "first_durably_recorded_http_success"
V3_CAMPAIGN_MANIFEST_KIND = "v3-development-campaign-manifest"
_ABSENT_SHA256 = "absent"
_REQUEST_CONTRACT_KEYS = frozenset(
    {
        "adapter",
        "endpoint_sha256",
        "request_model",
        "seed_supported",
        "timeout_seconds",
        "transport_profile",
        "static_request_extensions_sha256",
        "response_format",
    }
)
_RESPONSE_CONTRACT_KEYS = frozenset(
    {
        "provider_models",
        "finish_reasons",
        "max_output_tokens",
        "seed_supported",
        "require_zero_reasoning_tokens",
        "prompt_cache_mode",
        "provider_fingerprint_mode",
        "provider_fingerprint_sha256",
    }
)
_CANARY_EVIDENCE_KEYS = frozenset(
    {
        "status",
        "artifact_sha256",
        "route_binding_sha256",
        "contract_satisfied",
    }
)
_MODEL_BINDING_KEYS = frozenset(
    {
        "provider",
        "name",
        "snapshot",
        "sanitized_request_contract",
        "accepted_response_contract",
        "canary_evidence",
    }
)
_RUNTIME_ROUTE_KEYS = frozenset(
    {"sanitized_request_contract", "accepted_response_contract"}
)
_PLAN_ENTRY_KEYS = frozenset(
    {
        "shard_index",
        "phase",
        "phase_shard_index",
        "development_outcome_eligible",
        "model_index",
        "model_stratum",
        "model_binding_sha256",
        "route_binding_sha256",
        "world_index",
        "world_seed",
        "depth",
        "world_hash",
        "arm_position",
        "arm_id",
        "arm_hash",
        "logical_calls",
        "sampling_base_seed",
        "run_id",
        "plan_entry_sha256",
    }
)
_CAMPAIGN_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "experiment",
        "protocol_version",
        "config_sha256",
        "frozen_config",
        "source_manifest_sha256",
        "source_manifest",
        "execution_plan_sha256",
        "accepted_attempt_estimand",
        "transaction_unit",
        "total_shards",
        "total_logical_calls",
        "route_contracts",
        "execution_plan",
    }
)
_SOURCE_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "created_at_utc",
        "source_manifest_sha256",
        "files",
        "environment",
    }
)
_SOURCE_FILE_KEYS = frozenset({"path", "size_bytes", "sha256"})
_SOURCE_ENVIRONMENT_KEYS = frozenset(
    {
        "python_version",
        "python_implementation",
        "python_executable",
        "platform",
        "git_head",
    }
)


def _route_pre_execution_fields(index: int) -> list[str]:
    prefix = f"model_strata[{index}]"
    return [
        f"{prefix}.provider",
        f"{prefix}.name",
        f"{prefix}.snapshot",
        f"{prefix}.route_contract.sanitized_request_contract.endpoint_sha256",
        f"{prefix}.route_contract.sanitized_request_contract.request_model",
        f"{prefix}.route_contract.sanitized_request_contract.seed_supported",
        f"{prefix}.route_contract.sanitized_request_contract.transport_profile",
        f"{prefix}.route_contract.sanitized_request_contract.static_request_extensions_sha256",
        f"{prefix}.route_contract.accepted_response_contract.provider_models",
        f"{prefix}.route_contract.accepted_response_contract.seed_supported",
        f"{prefix}.route_contract.accepted_response_contract.require_zero_reasoning_tokens",
        f"{prefix}.route_contract.accepted_response_contract.prompt_cache_mode",
        f"{prefix}.route_contract.accepted_response_contract.provider_fingerprint_mode",
        f"{prefix}.route_contract.accepted_response_contract.provider_fingerprint_sha256",
        f"{prefix}.route_contract.route_binding_sha256",
        f"{prefix}.canary_evidence.status",
        f"{prefix}.canary_evidence.artifact_sha256",
        f"{prefix}.canary_evidence.route_binding_sha256",
        f"{prefix}.canary_evidence.contract_satisfied",
    ]


_EXPECTED_PRE_EXECUTION_FIELDS = [
    *(_route_pre_execution_fields(0)),
    *(_route_pre_execution_fields(1)),
    "execution.execution_plan_sha256",
    "execution.source_manifest_sha256",
]
_EXPECTED_ARMS = {
    "L": {"kind": "fixed", "temperature": 0.2},
    "H": {"kind": "fixed", "temperature": 1.2},
    "C": {
        "kind": "sequence",
        "temperatures": [1.2, 0.2, 1.2, 0.2, 0.2],
    },
    "E2": {
        "kind": "adaptive",
        "controller_version": "validity-novelty-v2",
        "initial_temperature": 1.0,
        "minimum_temperature": 0.2,
        "maximum_temperature": 1.2,
        "improvement_step": -0.2,
        "stagnation_step": 0.3,
        "minimum_valid_candidates": 3,
        "minimum_useful_new_behaviors": 1,
        "useful_novelty_score_tolerance": 1.0 / 12.0,
        "decision_precedence": [
            "low_validity_decrease",
            "probe_improved_decrease",
            "probe_ceiling_hold",
            "useful_novelty_hold",
            "stale_search_increase",
        ],
    },
}


class V3DevelopmentError(ValueError):
    """Raised when a v3 template, binding, or execution plan is unsafe."""


def _detached(value: Any) -> Any:
    try:
        return json.loads(canonical_json_bytes(value).decode("utf-8"))
    except Exception as exc:
        raise V3DevelopmentError("v3 design must be finite JSON") from exc


def _sha256(name: str, value: Any) -> str:
    if not (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    ):
        raise V3DevelopmentError(f"{name} must be a lowercase SHA-256")
    return value


def _nonempty(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V3DevelopmentError(f"{name} must be a non-empty string")
    return value


def _exact_keys(name: str, value: Mapping[str, Any], expected: frozenset[str]) -> None:
    observed = set(value)
    if observed != set(expected):
        raise V3DevelopmentError(
            f"{name} fields drifted: missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )


def _boolean(name: str, value: Any) -> bool:
    if type(value) is not bool:
        raise V3DevelopmentError(f"{name} must be a boolean")
    return value


def _string_list(name: str, value: Any) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise V3DevelopmentError(
            f"{name} must be a non-empty duplicate-free string array"
        )
    return list(value)


def _normalize_source_manifest(value: Any) -> dict[str, Any]:
    """Validate the complete file-by-file provenance envelope."""

    if not isinstance(value, Mapping):
        raise V3DevelopmentError("source_manifest must be an object")
    result = _detached(value)
    _exact_keys("source_manifest", result, _SOURCE_MANIFEST_KEYS)
    if result.get("schema_version") != 1:
        raise V3DevelopmentError("source_manifest schema version drifted")
    _nonempty("source_manifest.created_at_utc", result.get("created_at_utc"))
    files = result.get("files")
    if not isinstance(files, list) or not files:
        raise V3DevelopmentError("source_manifest.files must be a non-empty array")
    paths: list[str] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, Mapping):
            raise V3DevelopmentError("source_manifest file entry must be an object")
        _exact_keys(
            f"source_manifest.files[{index}]",
            entry,
            _SOURCE_FILE_KEYS,
        )
        path = _nonempty(
            f"source_manifest.files[{index}].path",
            entry.get("path"),
        )
        if (
            path.startswith("/")
            or "\\" in path
            or "\x00" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise V3DevelopmentError("source_manifest file path is not normalized")
        size = entry.get("size_bytes")
        if type(size) is not int or size < 0:
            raise V3DevelopmentError(
                "source_manifest file size must be a non-negative integer"
            )
        _sha256(
            f"source_manifest.files[{index}].sha256",
            entry.get("sha256"),
        )
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise V3DevelopmentError(
            "source_manifest file paths must be sorted and duplicate-free"
        )
    observed_hash = _sha256(
        "source_manifest.source_manifest_sha256",
        result.get("source_manifest_sha256"),
    )
    if observed_hash != sha256_json(files):
        raise V3DevelopmentError("source_manifest file-list hash drifted")
    environment = result.get("environment")
    if not isinstance(environment, Mapping):
        raise V3DevelopmentError("source_manifest.environment must be an object")
    _exact_keys(
        "source_manifest.environment",
        environment,
        _SOURCE_ENVIRONMENT_KEYS,
    )
    for field in (
        "python_version",
        "python_implementation",
        "python_executable",
        "platform",
    ):
        _nonempty(f"source_manifest.environment.{field}", environment.get(field))
    git_head = environment.get("git_head")
    if git_head is not None:
        _nonempty("source_manifest.environment.git_head", git_head)
    return result


def _template_request_contract() -> dict[str, Any]:
    return {
        "adapter": "openai-compatible-chat-completions-v1",
        "endpoint_sha256": None,
        "request_model": None,
        "seed_supported": None,
        "timeout_seconds": 120.0,
        "transport_profile": None,
        "static_request_extensions_sha256": None,
        "response_format": "json_object",
    }


def _template_response_contract() -> dict[str, Any]:
    return {
        "provider_models": None,
        "finish_reasons": ["stop"],
        "max_output_tokens": 256,
        "seed_supported": None,
        "require_zero_reasoning_tokens": None,
        "prompt_cache_mode": None,
        "provider_fingerprint_mode": None,
        "provider_fingerprint_sha256": None,
    }


def _normalize_request_contract(
    value: Any,
    *,
    name: str,
    require_bound: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V3DevelopmentError(f"{name} must be an object")
    result = _detached(value)
    _exact_keys(name, result, _REQUEST_CONTRACT_KEYS)
    if result.get("adapter") != "openai-compatible-chat-completions-v1":
        raise V3DevelopmentError(f"{name}.adapter drifted")
    if result.get("timeout_seconds") != 120.0:
        raise V3DevelopmentError(f"{name}.timeout_seconds drifted")
    if result.get("response_format") != "json_object":
        raise V3DevelopmentError(f"{name}.response_format drifted")
    route_fields = (
        "endpoint_sha256",
        "request_model",
        "seed_supported",
        "transport_profile",
        "static_request_extensions_sha256",
    )
    if not require_bound:
        if any(result.get(field) is not None for field in route_fields):
            raise V3DevelopmentError(f"template {name} route fields must remain null")
        return result
    _sha256(f"{name}.endpoint_sha256", result.get("endpoint_sha256"))
    _nonempty(f"{name}.request_model", result.get("request_model"))
    _boolean(f"{name}.seed_supported", result.get("seed_supported"))
    if result.get("transport_profile") != "stdlib-urllib-one-shot-v1":
        raise V3DevelopmentError(f"{name}.transport_profile drifted")
    _sha256(
        f"{name}.static_request_extensions_sha256",
        result.get("static_request_extensions_sha256"),
    )
    return result


def _normalize_response_contract(
    value: Any,
    *,
    name: str,
    require_bound: bool,
    for_storage: bool,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V3DevelopmentError(f"{name} must be an object")
    result = _detached(value)
    _exact_keys(name, result, _RESPONSE_CONTRACT_KEYS)
    if result.get("finish_reasons") != ["stop"]:
        raise V3DevelopmentError(f"{name}.finish_reasons drifted")
    if result.get("max_output_tokens") != 256:
        raise V3DevelopmentError(f"{name}.max_output_tokens drifted")
    route_fields = (
        "provider_models",
        "seed_supported",
        "require_zero_reasoning_tokens",
        "prompt_cache_mode",
        "provider_fingerprint_mode",
        "provider_fingerprint_sha256",
    )
    if not require_bound:
        if any(result.get(field) is not None for field in route_fields):
            raise V3DevelopmentError(f"template {name} route fields must remain null")
        return result
    result["provider_models"] = _string_list(
        f"{name}.provider_models", result.get("provider_models")
    )
    _boolean(f"{name}.seed_supported", result.get("seed_supported"))
    _boolean(
        f"{name}.require_zero_reasoning_tokens",
        result.get("require_zero_reasoning_tokens"),
    )
    if result.get("prompt_cache_mode") not in {"absent", "complete"}:
        raise V3DevelopmentError(
            f"{name}.prompt_cache_mode must be 'absent' or 'complete'"
        )
    fingerprint_mode = result.get("provider_fingerprint_mode")
    fingerprint_hash = result.get("provider_fingerprint_sha256")
    if fingerprint_mode == "absent":
        if fingerprint_hash is not None and fingerprint_hash != _ABSENT_SHA256:
            raise V3DevelopmentError(
                f"{name}.provider_fingerprint_sha256 must mark absence"
            )
        result["provider_fingerprint_sha256"] = (
            _ABSENT_SHA256 if for_storage else None
        )
    elif fingerprint_mode == "exact_sha256":
        _sha256(f"{name}.provider_fingerprint_sha256", fingerprint_hash)
    else:
        raise V3DevelopmentError(
            f"{name}.provider_fingerprint_mode must be 'absent' or 'exact_sha256'"
        )
    return result


def derive_route_binding_sha256(
    sanitized_request_contract: Mapping[str, Any],
    accepted_response_contract: Mapping[str, Any],
) -> str:
    """Derive the exact hash used by the live v3 logical-slot coordinator."""

    request = _normalize_request_contract(
        sanitized_request_contract,
        name="sanitized_request_contract",
        require_bound=True,
    )
    response = _normalize_response_contract(
        accepted_response_contract,
        name="accepted_response_contract",
        require_bound=True,
        for_storage=False,
    )
    return sha256_json(
        {
            "coordinator_version": V3_COORDINATOR_VERSION,
            "accepted_attempt_estimand": V3_ACCEPTED_ATTEMPT_ESTIMAND,
            "request_contract": request,
            "response_contract": response,
        }
    )


def _normalize_canary_evidence(
    value: Any,
    *,
    name: str,
    require_bound: bool,
    route_binding_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise V3DevelopmentError(f"{name} must be an object")
    result = _detached(value)
    _exact_keys(name, result, _CANARY_EVIDENCE_KEYS)
    if not require_bound:
        if any(item is not None for item in result.values()):
            raise V3DevelopmentError(f"template {name} fields must remain null")
        return result
    if result.get("status") != "passed":
        raise V3DevelopmentError(f"{name}.status must be 'passed'")
    _sha256(f"{name}.artifact_sha256", result.get("artifact_sha256"))
    observed_binding = _sha256(
        f"{name}.route_binding_sha256",
        result.get("route_binding_sha256"),
    )
    if route_binding_sha256 is not None and observed_binding != route_binding_sha256:
        raise V3DevelopmentError(f"{name} does not bind the frozen route")
    if result.get("contract_satisfied") is not True:
        raise V3DevelopmentError(f"{name}.contract_satisfied must be true")
    return result


def _validate_stratum_route(
    stratum: Mapping[str, Any],
    *,
    index: int,
    require_bound: bool,
) -> tuple[str | None, str | None]:
    prefix = f"model_strata[{index}]"
    route = stratum.get("route_contract")
    if not isinstance(route, Mapping) or set(route) != {
        "schema_version",
        "sanitized_request_contract",
        "accepted_response_contract",
        "route_binding_sha256",
    }:
        raise V3DevelopmentError(f"{prefix}.route_contract fields drifted")
    if route.get("schema_version") != V3_ROUTE_CONTRACT_SCHEMA_VERSION:
        raise V3DevelopmentError(f"{prefix}.route_contract version drifted")
    request = _normalize_request_contract(
        route.get("sanitized_request_contract"),
        name=f"{prefix}.route_contract.sanitized_request_contract",
        require_bound=require_bound,
    )
    response = _normalize_response_contract(
        route.get("accepted_response_contract"),
        name=f"{prefix}.route_contract.accepted_response_contract",
        require_bound=require_bound,
        for_storage=True,
    )
    stored_binding = route.get("route_binding_sha256")
    if not require_bound:
        if stored_binding is not None:
            raise V3DevelopmentError(
                f"template {prefix}.route_contract.route_binding_sha256 must remain null"
            )
        _normalize_canary_evidence(
            stratum.get("canary_evidence"),
            name=f"{prefix}.canary_evidence",
            require_bound=False,
        )
        return None, None
    if any(item is None for item in request.values()) or any(
        item is None for item in response.values()
    ):
        raise V3DevelopmentError(f"{prefix} frozen route contains null placeholders")
    derived = derive_route_binding_sha256(request, response)
    if _sha256(f"{prefix}.route_binding_sha256", stored_binding) != derived:
        raise V3DevelopmentError(f"{prefix} derived route binding drifted")
    if request["request_model"] != stratum.get("name"):
        raise V3DevelopmentError(f"{prefix} request model does not match name")
    if response["provider_models"] != [stratum.get("snapshot")]:
        raise V3DevelopmentError(
            f"{prefix} response model contract must equal the frozen snapshot"
        )
    if response["seed_supported"] is not request["seed_supported"]:
        raise V3DevelopmentError(f"{prefix} request/response seed capability drifted")
    canary = _normalize_canary_evidence(
        stratum.get("canary_evidence"),
        name=f"{prefix}.canary_evidence",
        require_bound=True,
        route_binding_sha256=derived,
    )
    return derived, str(canary["artifact_sha256"])


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V3DevelopmentError(f"cannot read {label}") from exc
    if not isinstance(value, Mapping):
        raise V3DevelopmentError(f"{label} must be a JSON object")
    return dict(value)


def _registered_development_seeds() -> set[int]:
    value = _load_json(DEVELOPMENT_SEED_REGISTRY_PATH, "development seed registry")
    seeds = value.get("seeds")
    if not isinstance(seeds, list) or any(type(seed) is not int for seed in seeds):
        raise V3DevelopmentError("development seed registry is malformed")
    if len(seeds) != len(set(seeds)):
        raise V3DevelopmentError("development seed registry contains duplicates")
    return set(seeds)


def validate_v3_config(
    value: Mapping[str, Any],
    *,
    require_bound: bool,
) -> dict[str, Any]:
    """Validate either the checked-in template or a fully frozen instance."""

    if type(require_bound) is not bool:
        raise TypeError("require_bound must be a boolean")
    config = _detached(value)
    if config.get("schema_version") != 1 or config.get("protocol_version") != 3:
        raise V3DevelopmentError("v3 schema/protocol version drifted")
    expected_status = (
        "frozen-development-v3"
        if require_bound
        else "template-pre-execution-freeze-required"
    )
    if config.get("status") != expected_status:
        raise V3DevelopmentError(f"v3 status must be {expected_status!r}")
    if config.get("specification") != "v3-development-spec.md":
        raise V3DevelopmentError("v3 specification binding drifted")
    expected_pre_execution = [] if require_bound else _EXPECTED_PRE_EXECUTION_FIELDS
    if config.get("pre_execution_required") != expected_pre_execution:
        raise V3DevelopmentError("v3 unresolved-field registry drifted")
    independence = config.get("independence")
    if not isinstance(independence, Mapping) or any(
        independence.get(key) is not False
        for key in (
            "continues_staged_v2_s3",
            "may_modify_staged_v2_s3",
            "may_pool_v2_outcomes",
        )
    ):
        raise V3DevelopmentError("v3/v2 independence contract drifted")

    strata = config.get("model_strata")
    if not isinstance(strata, list) or [
        item.get("stratum_id") if isinstance(item, Mapping) else None
        for item in strata
    ] != list(V3_MODEL_STRATA):
        raise V3DevelopmentError("v3 model strata or order drifted")
    route_bindings: list[str] = []
    bound_identities: list[tuple[str, str, str]] = []
    for index, stratum in enumerate(strata):
        if not isinstance(stratum, Mapping):
            raise V3DevelopmentError("v3 model stratum must be an object")
        if set(stratum) != {
            "stratum_id",
            "expected_model_family",
            "route_requirement",
            "provider",
            "name",
            "snapshot",
            "structured_output",
            "pre_execution_freeze_required",
            "route_contract",
            "canary_evidence",
        }:
            raise V3DevelopmentError("v3 model stratum fields drifted")
        for field in ("provider", "name", "snapshot"):
            item = stratum.get(field)
            if require_bound:
                _nonempty(f"model_strata[{index}].{field}", item)
            elif item is not None:
                raise V3DevelopmentError(
                    f"template model_strata[{index}].{field} must remain null"
                )
        if stratum.get("structured_output") is not True:
            raise V3DevelopmentError("every v3 model must require structured output")
        expected_family = ("DeepSeek v4", "GLM-5.2")[index]
        if (
            stratum.get("expected_model_family") != expected_family
            or stratum.get("route_requirement")
            != "official-route-canary-before-freeze"
        ):
            raise V3DevelopmentError("model family/route requirement drifted")
        if stratum.get("pre_execution_freeze_required") is not (not require_bound):
            raise V3DevelopmentError("model pre-execution freeze marker drifted")
        route_binding, _ = _validate_stratum_route(
            stratum,
            index=index,
            require_bound=require_bound,
        )
        if route_binding is not None:
            route_bindings.append(route_binding)
            bound_identities.append(
                (
                    str(stratum["provider"]),
                    str(stratum["name"]),
                    str(stratum["snapshot"]),
                )
            )
    if require_bound and (
        len(set(route_bindings)) != len(V3_MODEL_STRATA)
        or len(set(bound_identities)) != len(V3_MODEL_STRATA)
    ):
        raise V3DevelopmentError("v3 model strata must bind distinct exact routes")

    gate = config.get("gate_world")
    if gate != {
        "seed": 2000,
        "depth": 3,
        "development_outcome_eligible": False,
    }:
        raise V3DevelopmentError("v3 gate world drifted")
    worlds = config.get("worlds")
    expected_worlds = [
        {"seed": seed, "depth": 3 + ((seed - 2001) % 3)}
        for seed in range(2001, 2013)
    ]
    if worlds != expected_worlds:
        raise V3DevelopmentError("v3 fresh world grid drifted")
    if not ({2000, *range(2001, 2013)} <= _registered_development_seeds()):
        raise V3DevelopmentError("v3 seeds are not all excluded from confirmation")
    if config.get("episode") != {
        "rounds": 5,
        "candidates_per_round": 4,
        "max_output_tokens": 256,
        "archive_size": 4,
        "max_counterexamples_per_round": 2,
    }:
        raise V3DevelopmentError("v3 episode contract drifted")
    arms = config.get("arms")
    if not isinstance(arms, Mapping) or arms != _EXPECTED_ARMS:
        raise V3DevelopmentError("v3 arm definitions drifted")
    # Reuse the experiment validator for all policy-specific values.  A null
    # model is accepted only for the inert template.
    for stratum in strata:
        derive_stratum_config(config, str(stratum["stratum_id"]), gate=False)

    screen = config.get("compatibility_screen")
    if not isinstance(screen, Mapping) or (
        screen.get("seed_is_independent_evidence") is not False
        or screen.get("both_models_must_pass") is not True
        or screen.get("minimum_overall_search_valid_rate") != 0.95
        or screen.get("minimum_per_arm_search_valid_rate") != 0.9
        or screen.get("failure_status")
        != "compatibility_screen_failed_no_main_grid"
        or screen.get("manipulation")
        != {
            "canonical_unique_yield_per_planned_call": "H_strictly_greater_than_L",
            "behavioral_unique_yield_per_planned_call": "H_strictly_greater_than_L",
        }
    ):
        raise V3DevelopmentError("v3 compatibility screen drifted")
    diagnostics = config.get("development_diagnostics")
    if not isinstance(diagnostics, Mapping) or (
        diagnostics.get("minimum_overall_search_valid_rate") != 0.95
        or diagnostics.get("minimum_per_arm_search_valid_rate") != 0.9
        or diagnostics.get("low_validity_status")
        != "construct_validity_warning"
        or diagnostics.get("manipulation_failure_status")
        != "manipulation_indeterminate"
        or diagnostics.get("performance_classification_still_reported") is not True
    ):
        raise V3DevelopmentError("v3 development diagnostics drifted")
    if diagnostics.get("manipulation") != {
        "canonical_unique_yield_per_planned_call": "H_strictly_greater_than_L",
        "behavioral_unique_yield_per_planned_call": "H_strictly_greater_than_L",
    }:
        raise V3DevelopmentError("v3 manipulation gate drifted")
    endpoint = config.get("terminal_endpoint")
    if not isinstance(endpoint, Mapping) or (
        endpoint.get("primary_endpoint_failure") is not True
        or endpoint.get("primary_analysis_score") != 0.0
        or endpoint.get("zero_is_observed_accuracy") is not False
        or endpoint.get("content_regeneration_allowed") is not False
        or endpoint.get("observed_private_test_accuracy") != "null"
        or endpoint.get("all_invalid_definition")
        != "zero_search_valid_candidates_after_20_planned_calls"
    ):
        raise V3DevelopmentError("v3 all-invalid endpoint drifted")
    execution = config.get("execution")
    if not isinstance(execution, Mapping) or (
        execution.get("main_logical_calls") != 1920
        or execution.get("gate_logical_calls") != 160
        or execution.get("sampling_base_seed") != 1729
        or execution.get("max_physical_attempts_per_logical_slot") != 3
        or execution.get("request_timeout_seconds") != 120.0
        or execution.get("accepted_attempt_semantics")
        != "first_durably_recorded_http_success"
        or execution.get("content_retry_count_required") != 0
        or execution.get("content_retry_supported") is not False
        or execution.get(
            "first_durably_recorded_http_success_terminates_attempt_sequence"
        )
        is not True
        or execution.get("provider_contract_failure_after_http_success")
        != "fatal_no_retry"
        or execution.get("retryable_transport_categories")
        != [
            "timeout",
            "dns",
            "tls",
            "connection_refused",
            "connection_reset",
            "network_io",
        ]
        or execution.get("retryable_http_statuses") != ["429", "500-599"]
        or execution.get("durable_start_without_outcome")
        != "unresolved_engineering_indeterminate_no_automatic_retry"
    ):
        raise V3DevelopmentError("v3 execution/retry contract drifted")
    if config.get("primary_reference_arm") != "C":
        raise V3DevelopmentError("v3 primary reference must remain C")
    classification = config.get("classification")
    if not isinstance(classification, Mapping) or (
        classification.get("minimum_important_effect") != 0.05
        or classification.get("two_route_weighting") != "equal_stratum_weight"
        or classification.get("two_route_development_promising")
        != "both_model_contrasts_strictly_positive_and_equal_stratum_mean_at_least_0.05"
        or classification.get("two_route_nonpositive_development_signal")
        != "both_model_contrasts_less_than_or_equal_to_zero"
        or classification.get("mixed_or_small_development_signal")
        != "all_other_complete_grid_results"
    ):
        raise V3DevelopmentError("v3 classification rule drifted")
    statistical = config.get("statistical_analysis")
    if not isinstance(statistical, Mapping) or (
        statistical.get("primary_unit") != "world"
        or statistical.get("joint_cluster_count") != 12
        or statistical.get("episode_score_representation")
        != "integer_correct_out_of_64"
        or statistical.get("floating_threshold_tolerance") != 0.0
        or statistical.get("partial_campaign_classification") != "none"
    ):
        raise V3DevelopmentError("v3 statistical analysis plan drifted")
    if statistical.get("bootstrap") != {
        "kind": "depth_stratified_world_cluster_percentile",
        "percentile_method": "nearest_rank_order_statistic",
        "replicates": 100000,
        "rng_seed": 20260809,
        "confidence_level": 0.95,
        "resample_worlds_per_depth": 4,
        "preserve_both_routes_and_all_arms": True,
    }:
        raise V3DevelopmentError("v3 bootstrap plan drifted")
    if statistical.get("sign_flip") != {
        "kind": "exact_two_sided_paired_world",
        "patterns": 4096,
        "exploratory": True,
        "multiple_testing_adjustment": "none",
    }:
        raise V3DevelopmentError("v3 sign-flip plan drifted")
    if require_bound:
        _sha256(
            "execution.execution_plan_sha256",
            execution.get("execution_plan_sha256"),
        )
        _sha256(
            "execution.source_manifest_sha256",
            execution.get("source_manifest_sha256"),
        )
        expected_plan_hash = sha256_json(build_execution_plan(config))
        if execution.get("execution_plan_sha256") != expected_plan_hash:
            raise V3DevelopmentError("v3 execution-plan binding drifted")
    else:
        if execution.get("execution_plan_sha256") is not None or execution.get(
            "source_manifest_sha256"
        ) is not None:
            raise V3DevelopmentError("template execution hashes must remain null")
    return config


def load_v3_template(path: str | Path = V3_TEMPLATE_PATH) -> dict[str, Any]:
    return validate_v3_config(
        _load_json(Path(path), "v3 development template"),
        require_bound=False,
    )


def derive_stratum_config(
    config: Mapping[str, Any],
    stratum_id: str,
    *,
    gate: bool,
) -> dict[str, Any]:
    """Derive the existing runner's single-model config for one stratum."""

    if type(gate) is not bool:
        raise TypeError("gate must be a boolean")
    strata = config.get("model_strata")
    if not isinstance(strata, Sequence) or isinstance(strata, (str, bytes)):
        raise V3DevelopmentError("model_strata must be an array")
    matches = [
        item
        for item in strata
        if isinstance(item, Mapping) and item.get("stratum_id") == stratum_id
    ]
    if len(matches) != 1:
        raise V3DevelopmentError("requested v3 model stratum is absent or duplicated")
    model = matches[0]
    worlds = [dict(config["gate_world"])] if gate else list(config["worlds"])
    if gate:
        worlds[0].pop("development_outcome_eligible", None)
    derived = {
        "schema_version": 1,
        "status": "development-only",
        "experiment": f"{config['experiment']}::{stratum_id}",
        "worlds": worlds,
        "episode": dict(config["episode"]),
        "arms": dict(config["arms"]),
        "model": {
            "provider": model.get("provider"),
            "name": model.get("name"),
            "snapshot": model.get("snapshot"),
            "structured_output": model.get("structured_output"),
        },
    }
    try:
        return validate_config(derived)
    except Exception as exc:
        raise V3DevelopmentError("derived single-model experiment config is invalid") from exc


def _design_basis(config: Mapping[str, Any]) -> dict[str, Any]:
    value = _detached(config)
    execution = value["execution"]
    execution["execution_plan_sha256"] = None
    return value


def build_execution_plan(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build the deterministic 8-gate + 96-main shard order."""

    value = _detached(config)
    # Bound identities and source hash are required; the plan hash itself is
    # temporarily null while this function computes it.
    if value.get("status") != "frozen-development-v3":
        raise V3DevelopmentError("execution plans require a frozen v3 instance")
    _sha256(
        "execution.source_manifest_sha256",
        value.get("execution", {}).get("source_manifest_sha256"),
    )
    for index, stratum in enumerate(value.get("model_strata", [])):
        for field in ("provider", "name", "snapshot"):
            _nonempty(f"model_strata[{index}].{field}", stratum.get(field))
        _validate_stratum_route(stratum, index=index, require_bound=True)
    design_sha256 = sha256_json(_design_basis(value))
    arm_hashes = {
        arm_id: sha256_json({"arm_id": arm_id, "spec": value["arms"][arm_id]})
        for arm_id in V3_ARM_IDS
    }
    model_hashes = {
        item["stratum_id"]: sha256_json(item) for item in value["model_strata"]
    }
    entries: list[dict[str, Any]] = []

    def append_entry(
        *,
        phase: str,
        phase_world_index: int,
        world_spec: Mapping[str, Any],
        arm_position: int,
        arm_id: str,
        model_index: int,
    ) -> None:
        world = generate_world(int(world_spec["seed"]), depth=int(world_spec["depth"]))
        stratum = value["model_strata"][model_index]
        shard_index = len(entries)
        entry = {
            "shard_index": shard_index,
            "phase": phase,
            "phase_shard_index": (
                shard_index
                if phase == "gate"
                else shard_index - V3_GATE_SHARDS
            ),
            "development_outcome_eligible": phase == "main",
            "model_index": model_index,
            "model_stratum": stratum["stratum_id"],
            "model_binding_sha256": model_hashes[stratum["stratum_id"]],
            "route_binding_sha256": stratum["route_contract"][
                "route_binding_sha256"
            ],
            "world_index": None if phase == "gate" else phase_world_index,
            "world_seed": int(world_spec["seed"]),
            "depth": int(world_spec["depth"]),
            "world_hash": str(world.world_hash),
            "arm_position": arm_position,
            "arm_id": arm_id,
            "arm_hash": arm_hashes[arm_id],
            "logical_calls": V3_CALLS_PER_SHARD,
            "sampling_base_seed": int(value["execution"]["sampling_base_seed"]),
        }
        entry["run_id"] = sha256_json(
            {
                "design_sha256": design_sha256,
                "shard_index": shard_index,
                "phase": phase,
                "model_stratum": entry["model_stratum"],
                "route_binding_sha256": entry["route_binding_sha256"],
                "world_hash": entry["world_hash"],
                "arm_hash": entry["arm_hash"],
                "sampling_base_seed": entry["sampling_base_seed"],
            }
        )
        entry["plan_entry_sha256"] = sha256_json(entry)
        entries.append(entry)

    gate_order = _arm_execution_order(value["arms"], 0)
    for arm_position, arm_id in enumerate(gate_order):
        model_order = (0, 1) if arm_position % 2 == 0 else (1, 0)
        for model_index in model_order:
            append_entry(
                phase="gate",
                phase_world_index=0,
                world_spec=value["gate_world"],
                arm_position=arm_position,
                arm_id=arm_id,
                model_index=model_index,
            )

    for world_index, world_spec in enumerate(value["worlds"]):
        arm_order = _arm_execution_order(value["arms"], world_index)
        for arm_position, arm_id in enumerate(arm_order):
            model_order = (
                (0, 1)
                if (world_index + arm_position) % 2 == 0
                else (1, 0)
            )
            for model_index in model_order:
                append_entry(
                    phase="main",
                    phase_world_index=world_index,
                    world_spec=world_spec,
                    arm_position=arm_position,
                    arm_id=arm_id,
                    model_index=model_index,
                )
    if len(entries) != V3_TOTAL_SHARDS:
        raise V3DevelopmentError("v3 plan does not contain exactly 104 shards")
    if sum(item["logical_calls"] for item in entries[:V3_GATE_SHARDS]) != 160:
        raise V3DevelopmentError("v3 gate plan budget drifted")
    if sum(item["logical_calls"] for item in entries[V3_GATE_SHARDS:]) != 1920:
        raise V3DevelopmentError("v3 main plan budget drifted")
    return entries


def freeze_v3_design(
    template: Mapping[str, Any],
    *,
    model_bindings: Mapping[str, Mapping[str, Any]],
    source_manifest_sha256: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Bind canary-derived exact routes and hash the complete offline plan."""

    value = validate_v3_config(template, require_bound=False)
    if set(model_bindings) != set(V3_MODEL_STRATA):
        raise V3DevelopmentError("model bindings must cover exactly both frozen strata")
    for stratum in value["model_strata"]:
        binding = model_bindings[stratum["stratum_id"]]
        if not isinstance(binding, Mapping) or set(binding) != set(_MODEL_BINDING_KEYS):
            raise V3DevelopmentError(
                "each model binding must contain the exact identity, route, and canary fields"
            )
        for field in ("provider", "name", "snapshot"):
            stratum[field] = _nonempty(
                f"{stratum['stratum_id']}.{field}", binding[field]
            )
        request = _normalize_request_contract(
            binding["sanitized_request_contract"],
            name=f"{stratum['stratum_id']}.sanitized_request_contract",
            require_bound=True,
        )
        response_for_hash = _normalize_response_contract(
            binding["accepted_response_contract"],
            name=f"{stratum['stratum_id']}.accepted_response_contract",
            require_bound=True,
            for_storage=False,
        )
        if request["request_model"] != stratum["name"]:
            raise V3DevelopmentError(
                f"{stratum['stratum_id']} request model does not match frozen name"
            )
        if response_for_hash["provider_models"] != [stratum["snapshot"]]:
            raise V3DevelopmentError(
                f"{stratum['stratum_id']} provider model must equal frozen snapshot"
            )
        if response_for_hash["seed_supported"] is not request["seed_supported"]:
            raise V3DevelopmentError(
                f"{stratum['stratum_id']} request/response seed capability drifted"
            )
        route_binding = derive_route_binding_sha256(request, response_for_hash)
        canary = _normalize_canary_evidence(
            binding["canary_evidence"],
            name=f"{stratum['stratum_id']}.canary_evidence",
            require_bound=True,
            route_binding_sha256=route_binding,
        )
        stratum["route_contract"] = {
            "schema_version": V3_ROUTE_CONTRACT_SCHEMA_VERSION,
            "sanitized_request_contract": request,
            "accepted_response_contract": _normalize_response_contract(
                response_for_hash,
                name=f"{stratum['stratum_id']}.accepted_response_contract",
                require_bound=True,
                for_storage=True,
            ),
            "route_binding_sha256": route_binding,
        }
        stratum["canary_evidence"] = canary
        stratum["pre_execution_freeze_required"] = False
    value["status"] = "frozen-development-v3"
    value["pre_execution_required"] = []
    value["execution"]["source_manifest_sha256"] = _sha256(
        "source_manifest_sha256", source_manifest_sha256
    )
    value["execution"]["execution_plan_sha256"] = None
    plan = build_execution_plan(value)
    value["execution"]["execution_plan_sha256"] = sha256_json(plan)
    frozen = validate_v3_config(value, require_bound=True)
    if build_execution_plan(frozen) != plan:
        raise V3DevelopmentError("v3 plan changed after self-binding its hash")
    return frozen, plan


def validate_live_v3_preflight(
    config: Mapping[str, Any],
    *,
    runtime_routes: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    """Fail closed on runtime route drift without making a network request.

    A caller passes ``generator.sanitized_request_contract()`` and the pure
    ``AcceptedResponseContract.to_dict()`` result for each stratum. Constructing
    those values is offline; this function performs no credential or transport
    access.
    """

    frozen = validate_v3_config(config, require_bound=True)
    if not isinstance(runtime_routes, Mapping) or set(runtime_routes) != set(
        V3_MODEL_STRATA
    ):
        raise V3DevelopmentError(
            "runtime routes must cover exactly both frozen model strata"
        )
    result: dict[str, str] = {}
    for index, stratum in enumerate(frozen["model_strata"]):
        stratum_id = str(stratum["stratum_id"])
        runtime = runtime_routes[stratum_id]
        if not isinstance(runtime, Mapping) or set(runtime) != set(
            _RUNTIME_ROUTE_KEYS
        ):
            raise V3DevelopmentError(
                f"runtime route {stratum_id} fields drifted"
            )
        observed_request = _normalize_request_contract(
            runtime["sanitized_request_contract"],
            name=f"runtime_routes.{stratum_id}.sanitized_request_contract",
            require_bound=True,
        )
        observed_response = _normalize_response_contract(
            runtime["accepted_response_contract"],
            name=f"runtime_routes.{stratum_id}.accepted_response_contract",
            require_bound=True,
            for_storage=True,
        )
        stored_route = stratum["route_contract"]
        if observed_request != stored_route["sanitized_request_contract"]:
            raise V3DevelopmentError(
                f"runtime request route drifted for stratum {stratum_id}"
            )
        if observed_response != stored_route["accepted_response_contract"]:
            raise V3DevelopmentError(
                f"runtime accepted-response contract drifted for stratum {stratum_id}"
            )
        observed_binding = derive_route_binding_sha256(
            observed_request,
            runtime["accepted_response_contract"],
        )
        stored_binding, _ = _validate_stratum_route(
            stratum,
            index=index,
            require_bound=True,
        )
        if observed_binding != stored_binding:
            raise V3DevelopmentError(
                f"runtime route-binding hash drifted for stratum {stratum_id}"
            )
        result[stratum_id] = observed_binding
    return result


def _validate_plan_entries(plan: Any) -> list[dict[str, Any]]:
    if not isinstance(plan, list) or len(plan) != V3_TOTAL_SHARDS:
        raise V3DevelopmentError("v3 execution plan must contain exactly 104 entries")
    result = _detached(plan)
    run_ids: set[str] = set()
    entry_hashes: set[str] = set()
    for index, entry in enumerate(result):
        if not isinstance(entry, Mapping):
            raise V3DevelopmentError("v3 plan entry must be an object")
        _exact_keys(f"execution_plan[{index}]", entry, _PLAN_ENTRY_KEYS)
        if entry.get("shard_index") != index:
            raise V3DevelopmentError("v3 plan shard indices are not contiguous")
        run_id = _sha256(f"execution_plan[{index}].run_id", entry.get("run_id"))
        entry_hash = _sha256(
            f"execution_plan[{index}].plan_entry_sha256",
            entry.get("plan_entry_sha256"),
        )
        basis = dict(entry)
        basis.pop("plan_entry_sha256")
        if sha256_json(basis) != entry_hash:
            raise V3DevelopmentError("v3 plan entry hash drifted")
        if run_id in run_ids or entry_hash in entry_hashes:
            raise V3DevelopmentError("v3 plan run or entry identity is duplicated")
        run_ids.add(run_id)
        entry_hashes.add(entry_hash)
        if entry.get("model_stratum") not in V3_MODEL_STRATA:
            raise V3DevelopmentError("v3 plan model stratum drifted")
        if entry.get("arm_id") not in V3_ARM_IDS:
            raise V3DevelopmentError("v3 plan arm drifted")
        if entry.get("sampling_base_seed") != 1729:
            raise V3DevelopmentError("v3 plan sampling seed drifted")
        if entry.get("logical_calls") != V3_CALLS_PER_SHARD:
            raise V3DevelopmentError("v3 plan logical-call budget drifted")
    return result


def build_campaign_manifest(
    config: Mapping[str, Any],
    execution_plan: Sequence[Mapping[str, Any]],
    *,
    source_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a deterministic, self-contained v3 campaign manifest payload."""

    frozen = validate_v3_config(config, require_bound=True)
    provenance = _normalize_source_manifest(source_manifest)
    if (
        provenance["source_manifest_sha256"]
        != frozen["execution"]["source_manifest_sha256"]
    ):
        raise V3DevelopmentError("campaign source manifest does not match config")
    plan = _validate_plan_entries(list(execution_plan))
    expected_plan = build_execution_plan(frozen)
    if plan != expected_plan:
        raise V3DevelopmentError("campaign manifest execution plan drifted")
    plan_hash = sha256_json(plan)
    if plan_hash != frozen["execution"]["execution_plan_sha256"]:
        raise V3DevelopmentError("campaign manifest plan hash drifted")
    routes = [
        {
            "model_stratum": stratum["stratum_id"],
            "expected_model_family": stratum["expected_model_family"],
            "provider": stratum["provider"],
            "name": stratum["name"],
            "snapshot": stratum["snapshot"],
            "route_contract": stratum["route_contract"],
            "canary_evidence": stratum["canary_evidence"],
        }
        for stratum in frozen["model_strata"]
    ]
    return {
        "schema_version": 1,
        "kind": V3_CAMPAIGN_MANIFEST_KIND,
        "experiment": frozen["experiment"],
        "protocol_version": 3,
        "config_sha256": sha256_json(frozen),
        "frozen_config": frozen,
        "source_manifest_sha256": frozen["execution"][
            "source_manifest_sha256"
        ],
        "source_manifest": provenance,
        "execution_plan_sha256": plan_hash,
        "accepted_attempt_estimand": V3_ACCEPTED_ATTEMPT_ESTIMAND,
        "transaction_unit": frozen["execution"]["transaction_unit"],
        "total_shards": V3_TOTAL_SHARDS,
        "total_logical_calls": sum(entry["logical_calls"] for entry in plan),
        "route_contracts": routes,
        "execution_plan": plan,
    }


def _validate_campaign_manifest_uncached(
    campaign_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate every self-contained config, source, route, and plan binding."""

    if not isinstance(campaign_manifest, Mapping):
        raise V3DevelopmentError("campaign manifest must be an object")
    manifest = _detached(campaign_manifest)
    _exact_keys("campaign_manifest", manifest, _CAMPAIGN_MANIFEST_KEYS)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != V3_CAMPAIGN_MANIFEST_KIND
        or manifest.get("protocol_version") != 3
        or manifest.get("accepted_attempt_estimand")
        != V3_ACCEPTED_ATTEMPT_ESTIMAND
    ):
        raise V3DevelopmentError("campaign manifest contract drifted")
    frozen_value = manifest.get("frozen_config")
    if not isinstance(frozen_value, Mapping):
        raise V3DevelopmentError("campaign manifest frozen_config must be an object")
    frozen = validate_v3_config(frozen_value, require_bound=True)
    config_hash = _sha256(
        "campaign_manifest.config_sha256",
        manifest.get("config_sha256"),
    )
    if config_hash != sha256_json(frozen):
        raise V3DevelopmentError("campaign manifest frozen-config hash drifted")
    provenance = _normalize_source_manifest(manifest.get("source_manifest"))
    source_hash = _sha256(
        "campaign_manifest.source_manifest_sha256",
        manifest.get("source_manifest_sha256"),
    )
    if not (
        source_hash
        == provenance["source_manifest_sha256"]
        == frozen["execution"]["source_manifest_sha256"]
    ):
        raise V3DevelopmentError("campaign manifest source binding drifted")
    plan = _validate_plan_entries(manifest.get("execution_plan"))
    expected_plan = build_execution_plan(frozen)
    if plan != expected_plan:
        raise V3DevelopmentError("campaign manifest execution plan drifted")
    plan_hash = _sha256(
        "campaign_manifest.execution_plan_sha256",
        manifest.get("execution_plan_sha256"),
    )
    if not (
        plan_hash
        == sha256_json(plan)
        == frozen["execution"]["execution_plan_sha256"]
    ):
        raise V3DevelopmentError("campaign manifest execution-plan hash drifted")
    expected_routes = [
        {
            "model_stratum": stratum["stratum_id"],
            "expected_model_family": stratum["expected_model_family"],
            "provider": stratum["provider"],
            "name": stratum["name"],
            "snapshot": stratum["snapshot"],
            "route_contract": stratum["route_contract"],
            "canary_evidence": stratum["canary_evidence"],
        }
        for stratum in frozen["model_strata"]
    ]
    if manifest.get("route_contracts") != expected_routes:
        raise V3DevelopmentError("campaign manifest route contracts drifted")
    if (
        manifest.get("experiment") != frozen["experiment"]
        or manifest.get("transaction_unit")
        != frozen["execution"]["transaction_unit"]
        or manifest.get("total_shards") != V3_TOTAL_SHARDS
        or manifest.get("total_logical_calls")
        != sum(entry["logical_calls"] for entry in plan)
    ):
        raise V3DevelopmentError("campaign manifest campaign metadata drifted")
    return manifest


@lru_cache(maxsize=8)
def _validated_campaign_manifest_bytes(encoded: bytes) -> bytes:
    """Cache only immutable canonical bytes, never a caller-owned object."""

    value = json.loads(encoded.decode("utf-8"))
    validated = _validate_campaign_manifest_uncached(value)
    return canonical_json_bytes(validated)


def validate_campaign_manifest(
    campaign_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a manifest, with content-addressed reuse for repeated audits.

    Campaign storage rechecks the same 104-entry manifest for every immutable
    artifact.  Keying the cache by canonical bytes keeps those checks cheap
    without trusting object identity: any mutation produces a different key
    and therefore runs the complete validator again.  Cached values are bytes
    and each caller receives a fresh detached object.
    """

    if not isinstance(campaign_manifest, Mapping):
        raise V3DevelopmentError("campaign manifest must be an object")
    detached = _detached(campaign_manifest)
    encoded = canonical_json_bytes(detached)
    return json.loads(_validated_campaign_manifest_bytes(encoded).decode("utf-8"))


def transaction_identity_payload(
    campaign_manifest: Mapping[str, Any],
    plan_entry: Mapping[str, Any],
) -> dict[str, Any]:
    """Map one manifest entry to ``FrozenTransactionIdentity`` constructor data."""

    manifest = validate_campaign_manifest(campaign_manifest)
    plan = manifest["execution_plan"]
    plan_hash = sha256_json(plan)
    if manifest.get("execution_plan_sha256") != plan_hash:
        raise V3DevelopmentError("campaign manifest execution-plan hash drifted")
    entry = _detached(plan_entry)
    shard_index = entry.get("shard_index")
    if type(shard_index) is not int or not 0 <= shard_index < len(plan):
        raise V3DevelopmentError("transaction plan entry has invalid shard index")
    if plan[shard_index] != entry:
        raise V3DevelopmentError("transaction plan entry is not manifest-bound")
    return {
        "campaign_manifest_payload_sha256": sha256_json(manifest),
        "execution_plan_sha256": plan_hash,
        "plan_entry_sha256": entry["plan_entry_sha256"],
        "run_id": entry["run_id"],
        "shard_index": entry["shard_index"],
        "model_stratum": entry["model_stratum"],
        "phase": entry["phase"],
        "world_seed": entry["world_seed"],
        "depth": entry["depth"],
        "arm_id": entry["arm_id"],
    }


def unresolved_preflight_fields(
    config: Mapping[str, Any],
) -> list[str]:
    """List inert-template fields that must be bound before any API call."""

    value = _detached(config)
    unresolved: list[str] = []
    for index, stratum in enumerate(value.get("model_strata", [])):
        prefix = f"model_strata[{index}]"
        for field in ("provider", "name", "snapshot"):
            if not stratum.get(field):
                unresolved.append(f"{prefix}.{field}")
        route = stratum.get("route_contract", {})
        request = route.get("sanitized_request_contract", {})
        for field in (
            "endpoint_sha256",
            "request_model",
            "seed_supported",
            "transport_profile",
            "static_request_extensions_sha256",
        ):
            if request.get(field) is None:
                unresolved.append(
                    f"{prefix}.route_contract.sanitized_request_contract.{field}"
                )
        response = route.get("accepted_response_contract", {})
        for field in (
            "provider_models",
            "seed_supported",
            "require_zero_reasoning_tokens",
            "prompt_cache_mode",
            "provider_fingerprint_mode",
            "provider_fingerprint_sha256",
        ):
            if response.get(field) is None:
                unresolved.append(
                    f"{prefix}.route_contract.accepted_response_contract.{field}"
                )
        if route.get("route_binding_sha256") is None:
            unresolved.append(f"{prefix}.route_contract.route_binding_sha256")
        canary = stratum.get("canary_evidence", {})
        for field in (
            "status",
            "artifact_sha256",
            "route_binding_sha256",
            "contract_satisfied",
        ):
            if canary.get(field) is None:
                unresolved.append(f"{prefix}.canary_evidence.{field}")
    execution = value.get("execution", {})
    for field in ("execution_plan_sha256", "source_manifest_sha256"):
        if not execution.get(field):
            unresolved.append(f"execution.{field}")
    return unresolved


__all__ = [
    "DEVELOPMENT_SEED_REGISTRY_PATH",
    "PROJECT_ROOT",
    "V3DevelopmentError",
    "V3_ARM_IDS",
    "V3_CALLS_PER_SHARD",
    "V3_GATE_SHARDS",
    "V3_MAIN_SHARDS",
    "V3_MODEL_STRATA",
    "V3_TEMPLATE_PATH",
    "V3_TOTAL_SHARDS",
    "V3_ACCEPTED_ATTEMPT_ESTIMAND",
    "V3_CAMPAIGN_MANIFEST_KIND",
    "V3_COORDINATOR_VERSION",
    "V3_ROUTE_CONTRACT_SCHEMA_VERSION",
    "build_campaign_manifest",
    "build_execution_plan",
    "derive_route_binding_sha256",
    "derive_stratum_config",
    "freeze_v3_design",
    "load_v3_template",
    "unresolved_preflight_fields",
    "validate_campaign_manifest",
    "validate_v3_config",
    "validate_live_v3_preflight",
    "transaction_identity_payload",
]
