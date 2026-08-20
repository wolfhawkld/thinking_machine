"""Research-only matched-triad replication for the spark closure study.

The public plan is target-free.  All three frozen 96-call route arms must finish and
pass their live response contracts before the built-in joint analyzer derives
one shared target per world.  There is no retry, resume, adaptive sampling, or
network access unless the CLI ``generate`` command is given ``--execute``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from . import dsl, spark_closure
from .credentials import ProviderCredentials, load_provider_credentials
from .provenance import PROJECT_ROOT, source_manifest
from .providers.openai_compatible import OpenAICompatibleGenerator
from .runner import GenerationResponse
from .staged_pilot_v3 import AcceptedResponseContract, route_binding_sha256
from .v3_live import V3LiveError, build_v3_generator, model_binding_from_canary


CROSS_MODEL_WORLD_COUNT = 32
CROSS_MODEL_CALLS_PER_WORLD = 3
CROSS_MODEL_CALLS_PER_ARM = 96
CROSS_MODEL_ARM_COUNT = 3
CROSS_MODEL_PROTOCOL_ID = "cross-model-matched-triad-v1"
# The matched triad deliberately reuses the unopened V1 task grid so the
# public worlds, motifs, targets, and slot identities do not move after route
# calibration.  Only the pre-target route protocol and execution order change.
CROSS_MODEL_SEED_NAMESPACE = "spark-closure-cross-model-paired-v1"
CROSS_MODEL_TARGET_SEED_NAMESPACE = CROSS_MODEL_SEED_NAMESPACE
CROSS_MODEL_MOTIF_SELECTION_NAMESPACE = CROSS_MODEL_SEED_NAMESPACE
CROSS_MODEL_EVIDENCE_SCOPE = (
    "prospective_matched_triad_cross_model_mechanism_replication"
)
CROSS_MODEL_WORLD_SEEDS = (
    5609854509399487714,
    8058848814949332127,
    7432589210973578845,
    3920682316420328816,
    1418744941558891841,
    7604204542873609924,
    1387282349159788876,
    8242426922921378803,
    1160497852689591359,
    6872575636001638699,
    7396720935553072228,
    5279887130524777443,
    5123783953932712497,
    3034756861122824323,
    2262333810103905472,
    518707974867583009,
    7993937249025442561,
    3850349365944176259,
    7211834526608777947,
    6627891344710956940,
    4402357155133626695,
    4960748528416202938,
    5566094773751083457,
    3680507740242696405,
    6866785901476227762,
    5033621553926766983,
    5357853615180860507,
    3120120567224792408,
    1045602656972176335,
    2858014253687291177,
    1789187785946847608,
    6476484620047087171,
)

DEEPSEEK_FLASH_ARM_ID = "deepseek-flash"
DEEPSEEK_PRO_ARM_ID = "deepseek-pro"
GLM_ARM_ID = "glm-5.2"
CROSS_MODEL_ARM_IDS = (
    DEEPSEEK_FLASH_ARM_ID,
    DEEPSEEK_PRO_ARM_ID,
    GLM_ARM_ID,
)
# Backward-compatible Python aliases only.  Saved matched-triad artifacts use
# the neutral arm ids above and never serialize retired reference/comparison ids.
REFERENCE_ARM_ID = DEEPSEEK_FLASH_ARM_ID
COMPARISON_ARM_ID = GLM_ARM_ID

DEEPSEEK_CANARY_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-cross-model-canaries-20260820"
    / "deepseek-v4-flash-action-canary.json"
)
GLM_CANARY_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-cross-model-canaries-20260820-tencent"
    / "glm-5.2-action-canary.json"
)
DEEPSEEK_PRO_CANARY_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-cross-model-canaries-20260821-pro"
    / "deepseek-v4-pro-action-canary.json"
)
DEEPSEEK_CANARY_SHA256 = (
    "0516ffd54692097ec06f2278951aae20c0835a86bacdef9af80699508a7e4f6a"
)
GLM_CANARY_SHA256 = (
    "9523e9422ca67bea9ece8a0358a4a06bf6080d38656fbfedce9b84c899178210"
)
DEEPSEEK_PRO_CANARY_SHA256 = (
    "1f7b92dabcc8c4c562d89328dd6cb9c4119b8cbec6e287926728c96ce8c0fce6"
)
DEEPSEEK_ROUTE_BINDING_SHA256 = (
    "0f9971ca63a7ff619b163bb31baf763da652eab5642d8d3d9208646fb20c03fa"
)
GLM_ROUTE_BINDING_SHA256 = (
    "02243ec1c415c25c9938f4d4a209b8e3864212ce3952ae3715d8140e4c13a6e9"
)
DEEPSEEK_PRO_ROUTE_BINDING_SHA256 = (
    "d44699c6e1463c8f428c72e04585feac9cdaf20cd64a680109b1e4d1d9255936"
)

ACTION_CANARY_PROMPT_SET_SHA256 = (
    "41d3d8878f6da9c1c7543ee3e82f01910a1558448a1364f94ab7e278a67e5094"
)

LAYERED_V1_ARTIFACTS = {
    "protocol_id": spark_closure.LAYERED_PROTOCOL_ID,
    "plan_sha256": "45b22d0e1b1b7657bfa7ae016e315e1af980e5b8a8771b9768ed1bef9c13777d",
    "generation_sha256": "570e84a005b87925358e13c559ac890d437844fc5fb5f85922c4661d655e827d",
    "analysis_sha256": "b9f672c0d7bc117fdee71c701bc5e8fbc37741ec49ddd0139378bb5c76b6d691",
}

_DEEPSEEK_RESPONSE_CONTRACT = dict(
    spark_closure.PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT
)
_DEEPSEEK_PRO_RESPONSE_CONTRACT = {
    "provider_models": ["deepseek-v4-pro"],
    "finish_reasons": ["stop", "length"],
    "max_output_tokens": 256,
    "seed_supported": False,
    "require_zero_reasoning_tokens": True,
    "prompt_cache_mode": "complete",
    "provider_fingerprint_mode": "exact_sha256",
    "provider_fingerprint_sha256": (
        "a2cd55bf7e17b1daa413c2d3ce931256a1d0d5e65084859059777e2bbb546787"
    ),
}
_GLM_RESPONSE_CONTRACT = {
    "provider_models": ["glm-5.2"],
    "finish_reasons": ["stop", "length"],
    "max_output_tokens": 256,
    "seed_supported": False,
    "require_zero_reasoning_tokens": True,
    "prompt_cache_mode": "absent",
    "provider_fingerprint_mode": "absent",
    "provider_fingerprint_sha256": None,
}
_DEEPSEEK_REQUEST_CONTRACT = {
    "adapter": "openai-compatible-chat-completions-v1",
    "endpoint_sha256": "948f1ecb6b48f91adc4e110d0351cd172b16450e9936d358992e0dfad7b863f3",
    "request_model": "deepseek-v4-flash",
    "seed_supported": False,
    "timeout_seconds": 120.0,
    "static_request_extensions_sha256": (
        "b5870a3a4005fe6ccfc6195efbbebf92d7a2fb4d7534ed672272370e67dc1ad5"
    ),
    "response_format": "json_object",
    "transport_profile": "stdlib-urllib-one-shot-v1",
}
_DEEPSEEK_PRO_REQUEST_CONTRACT = {
    **_DEEPSEEK_REQUEST_CONTRACT,
    "request_model": "deepseek-v4-pro",
}
_GLM_REQUEST_CONTRACT = {
    "adapter": "openai-compatible-chat-completions-v1",
    "endpoint_sha256": "2095d8a5425aaf2ce7b1c8a4b63baecdc0ffc4851ac92810b191ee3b9194840c",
    "request_model": "glm-5.2",
    "seed_supported": False,
    "timeout_seconds": 120.0,
    "static_request_extensions_sha256": (
        "b5870a3a4005fe6ccfc6195efbbebf92d7a2fb4d7534ed672272370e67dc1ad5"
    ),
    "response_format": "json_object",
    "transport_profile": "stdlib-urllib-one-shot-v1",
}

_ROUTE_FREEZES = {
    DEEPSEEK_FLASH_ARM_ID: {
        "model_stratum": "official-deepseek-v4",
        "provider_profile": "deepseek-official-openai-compatible",
        "request_model": "deepseek-v4-flash",
        "response_model": "deepseek-v4-flash",
        "sanitized_request_contract": _DEEPSEEK_REQUEST_CONTRACT,
        "canary_path": DEEPSEEK_CANARY_PATH,
        "canary_artifact_sha256": DEEPSEEK_CANARY_SHA256,
        "route_binding_sha256": DEEPSEEK_ROUTE_BINDING_SHA256,
        "accepted_response_contract": _DEEPSEEK_RESPONSE_CONTRACT,
        "canary_plan_sha256": (
            "5f96e79b61c6edd8b87fac2837d3ee1b71bd4ad90655eb6983b6c12dcc3531bc"
        ),
    },
    DEEPSEEK_PRO_ARM_ID: {
        "model_stratum": "official-deepseek-v4-pro",
        "provider_profile": "deepseek-official-openai-compatible",
        "request_model": "deepseek-v4-pro",
        "response_model": "deepseek-v4-pro",
        "sanitized_request_contract": _DEEPSEEK_PRO_REQUEST_CONTRACT,
        "canary_path": DEEPSEEK_PRO_CANARY_PATH,
        "canary_artifact_sha256": DEEPSEEK_PRO_CANARY_SHA256,
        "route_binding_sha256": DEEPSEEK_PRO_ROUTE_BINDING_SHA256,
        "accepted_response_contract": _DEEPSEEK_PRO_RESPONSE_CONTRACT,
        "canary_plan_sha256": (
            "8ede1082eb9f3a70a46020c19af1d3dac01529e69e161066f16ed9764df09bc7"
        ),
    },
    GLM_ARM_ID: {
        "model_stratum": "tencent-tokenhub-glm-5.2",
        "provider_profile": "tencent-tokenhub-openai-compatible",
        "request_model": "glm-5.2",
        "response_model": "glm-5.2",
        "sanitized_request_contract": _GLM_REQUEST_CONTRACT,
        "canary_path": GLM_CANARY_PATH,
        "canary_artifact_sha256": GLM_CANARY_SHA256,
        "route_binding_sha256": GLM_ROUTE_BINDING_SHA256,
        "accepted_response_contract": _GLM_RESPONSE_CONTRACT,
        "canary_plan_sha256": (
            "5f96e79b61c6edd8b87fac2837d3ee1b71bd4ad90655eb6983b6c12dcc3531bc"
        ),
    },
}


class CrossModelError(ValueError):
    """A triad plan, route arm, or generation artifact is malformed."""


@dataclass(frozen=True)
class RouteArmSpec:
    """Frozen public description of one qualified model route arm."""

    arm_id: str
    model_stratum: str
    provider_profile: str
    request_model: str
    response_model: str
    sanitized_request_contract: Mapping[str, Any]
    canary_artifact_sha256: str
    route_binding_sha256: str
    accepted_response_contract: Mapping[str, Any] | AcceptedResponseContract

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "model_stratum": self.model_stratum,
            "provider_profile": self.provider_profile,
            "request_model": self.request_model,
            "response_model": self.response_model,
            "sanitized_request_contract": _json_clone(
                self.sanitized_request_contract,
                label="sanitized request contract",
            ),
            "canary_artifact_sha256": self.canary_artifact_sha256,
            "route_binding_sha256": self.route_binding_sha256,
            "accepted_response_contract": _accepted_response_contract(
                self.accepted_response_contract
            ).to_dict(),
        }


class CrossModelGenerator(Protocol):
    @property
    def model(self) -> str:
        ...

    def sanitized_request_contract(self) -> dict[str, Any]:
        ...

    def generate(
        self,
        prompt: str,
        *,
        temperature: float,
        max_output_tokens: int,
        round_index: int,
        candidate_index: int,
    ) -> GenerationResponse:
        ...


_ROUTE_FIELDS = (
    "arm_id",
    "model_stratum",
    "provider_profile",
    "request_model",
    "response_model",
    "sanitized_request_contract",
    "canary_artifact_sha256",
    "route_binding_sha256",
    "accepted_response_contract",
)
_RESPONSE_CONTRACT_FIELDS = (
    "provider_models",
    "finish_reasons",
    "max_output_tokens",
    "seed_supported",
    "require_zero_reasoning_tokens",
    "prompt_cache_mode",
    "provider_fingerprint_mode",
    "provider_fingerprint_sha256",
)
_RECORD_IDENTITY_FIELDS = (
    "serial_index",
    "slot_id",
    "world_index",
    "world_seed",
    "slot_index",
    "condition",
    "motif_id",
    "motif_stratum",
    "world_identity_sha256",
    "slot_identity_sha256",
)
_PUBLIC_WORLD_FIELDS = frozenset(
    {
        "world_index",
        "world_seed",
        "D0",
        "parent",
        "parent_canonical_hash",
        "allowed_paths",
    }
)
_PUBLIC_SLOT_FIELDS = frozenset(
    {
        "serial_index",
        "slot_id",
        "world_index",
        "world_seed",
        "slot_index",
        "condition",
        "motif_id",
        "motif_stratum",
        "motif",
        "motif_selection_sha256",
    }
)
_PUBLIC_D0_ROW_FIELDS = frozenset({"point", "label"})
_PUBLIC_ALLOWED_PATH_FIELDS = frozenset(
    {"path", "expected_old_subtree_hash", "old_subtree"}
)


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
        raise CrossModelError("cross-model artifacts must be canonical JSON") from exc


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _json_clone(value: Any, *, label: str) -> Any:
    try:
        return json.loads(_canonical_json_bytes(value).decode("utf-8"))
    except CrossModelError as exc:
        raise CrossModelError(f"{label} must be JSON-ready") from exc


def _is_lowercase_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _accepted_response_contract(
    value: Mapping[str, Any] | AcceptedResponseContract,
) -> AcceptedResponseContract:
    if isinstance(value, AcceptedResponseContract):
        return value
    if not isinstance(value, Mapping):
        raise CrossModelError("route arm accepted_response_contract must be an object")
    saved = _json_clone(value, label="accepted response contract")
    if not isinstance(saved, dict) or set(saved) != set(_RESPONSE_CONTRACT_FIELDS):
        raise CrossModelError(
            "route arm accepted_response_contract must use the canonical schema"
        )
    if not isinstance(saved["provider_models"], list) or not isinstance(
        saved["finish_reasons"], list
    ):
        raise CrossModelError(
            "route arm response aliases and finish reasons must be lists"
        )
    try:
        contract = AcceptedResponseContract(
            provider_models=tuple(saved["provider_models"]),
            finish_reasons=tuple(saved["finish_reasons"]),
            max_output_tokens=saved["max_output_tokens"],
            seed_supported=saved["seed_supported"],
            require_zero_reasoning_tokens=saved[
                "require_zero_reasoning_tokens"
            ],
            prompt_cache_mode=saved["prompt_cache_mode"],
            provider_fingerprint_mode=saved["provider_fingerprint_mode"],
            provider_fingerprint_sha256=saved[
                "provider_fingerprint_sha256"
            ],
        )
    except (TypeError, ValueError) as exc:
        raise CrossModelError("route arm response contract is malformed") from exc
    if contract.to_dict() != saved:
        raise CrossModelError("route arm response contract is not canonical")
    return contract


def route_arm_from_canary(
    arm_id: str,
    credentials: ProviderCredentials,
    *,
    canary_path: str | Path | None = None,
) -> RouteArmSpec:
    """Validate one live route against its exact audited canary artifact."""

    if arm_id not in _ROUTE_FREEZES:
        raise CrossModelError(f"unknown frozen cross-model arm: {arm_id!r}")
    frozen = _ROUTE_FREEZES[arm_id]
    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    if credentials.model != frozen["request_model"]:
        raise CrossModelError("route credentials name another request model")
    path = Path(frozen["canary_path"] if canary_path is None else canary_path)
    try:
        payload = path.read_bytes()
        artifact = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CrossModelError("cannot read frozen route canary") from exc
    if hashlib.sha256(payload).hexdigest() != frozen["canary_artifact_sha256"]:
        raise CrossModelError("route canary file SHA-256 differs from the freeze")
    if not isinstance(artifact, Mapping):
        raise CrossModelError("route canary must contain an object")
    identity = artifact.get("identity")
    if (
        artifact.get("schema_version") != 1
        or artifact.get("kind") != "v3-route-canary"
        or artifact.get("passed") is not True
        or artifact.get("contract_satisfied") is not True
        or artifact.get("stratum_id") != frozen["model_stratum"]
        or artifact.get("provider") != frozen["provider_profile"]
        or not isinstance(identity, Mapping)
        or identity.get("request_model") != frozen["request_model"]
        or identity.get("response_model") != frozen["response_model"]
        or artifact.get("sanitized_request_contract")
        != frozen["sanitized_request_contract"]
        or artifact.get("accepted_response_contract")
        != frozen["accepted_response_contract"]
        or artifact.get("route_binding_sha256")
        != frozen["route_binding_sha256"]
    ):
        raise CrossModelError("route canary identity or contract differs from the freeze")
    if (
        artifact.get("canary_profile") != "closure-action-grammar-v1"
        or artifact.get("canary_plan_sha256")
        != frozen["canary_plan_sha256"]
        or artifact.get("prompt_set_sha256")
        != ACTION_CANARY_PROMPT_SET_SHA256
    ):
        raise CrossModelError("route action-grammar canary profile differs")
    diagnostics = artifact.get("diagnostics")
    if (
        not isinstance(diagnostics, Mapping)
        or diagnostics.get("outer_schema_valid_count") != 12
        or diagnostics.get("factual_action_parse_valid_count") != 12
        or diagnostics.get("content_gate_passed") is not True
    ):
        raise CrossModelError("route action-grammar content gate differs")
    try:
        binding = model_binding_from_canary(
            path,
            credentials,
            expected_stratum_id=str(frozen["model_stratum"]),
        )
    except (KeyError, TypeError, ValueError, V3LiveError) as exc:
        raise CrossModelError("route canary failed live binding validation") from exc
    evidence = binding.get("canary_evidence")
    if (
        binding.get("provider") != frozen["provider_profile"]
        or binding.get("name") != frozen["request_model"]
        or binding.get("snapshot") != frozen["response_model"]
        or binding.get("sanitized_request_contract")
        != frozen["sanitized_request_contract"]
        or binding.get("accepted_response_contract")
        != frozen["accepted_response_contract"]
        or not isinstance(evidence, Mapping)
        or evidence.get("status") != "passed"
        or evidence.get("artifact_sha256")
        != frozen["canary_artifact_sha256"]
        or evidence.get("route_binding_sha256")
        != frozen["route_binding_sha256"]
        or evidence.get("contract_satisfied") is not True
    ):
        raise CrossModelError("validated route binding differs from the protocol")
    return RouteArmSpec(
        arm_id=arm_id,
        model_stratum=str(frozen["model_stratum"]),
        provider_profile=str(frozen["provider_profile"]),
        request_model=str(frozen["request_model"]),
        response_model=str(frozen["response_model"]),
        sanitized_request_contract=_json_clone(
            frozen["sanitized_request_contract"],
            label="frozen request contract",
        ),
        canary_artifact_sha256=str(frozen["canary_artifact_sha256"]),
        route_binding_sha256=str(frozen["route_binding_sha256"]),
        accepted_response_contract=_json_clone(
            frozen["accepted_response_contract"],
            label="frozen response contract",
        ),
    )


def _route_payload(value: RouteArmSpec | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, RouteArmSpec):
        payload = value.to_dict()
    elif isinstance(value, Mapping):
        payload = {field: value.get(field) for field in _ROUTE_FIELDS}
    else:
        raise CrossModelError("route arms must be RouteArmSpec objects or mappings")

    for field in (
        "arm_id",
        "model_stratum",
        "provider_profile",
        "request_model",
        "response_model",
        "canary_artifact_sha256",
        "route_binding_sha256",
    ):
        item = payload.get(field)
        if not isinstance(item, str) or not item:
            raise CrossModelError(f"route arm {field} must be a non-empty string")
    request_contract = payload.get("sanitized_request_contract")
    if not isinstance(request_contract, Mapping):
        raise CrossModelError("route arm sanitized_request_contract must be an object")
    payload["sanitized_request_contract"] = _json_clone(
        request_contract,
        label="sanitized request contract",
    )
    contract = _accepted_response_contract(payload.get("accepted_response_contract"))
    if contract.provider_models[0] != payload["response_model"]:
        raise CrossModelError(
            "route arm response_model differs from its accepted response contract"
        )
    payload["accepted_response_contract"] = contract.to_dict()
    for field in ("canary_artifact_sha256", "route_binding_sha256"):
        if not _is_lowercase_sha256(payload[field]):
            raise CrossModelError(f"route arm {field} must be a lowercase SHA-256")
    return payload


def _route_identity_sha256(route: Mapping[str, Any]) -> str:
    return _sha256_json(
        {field: route[field] for field in _ROUTE_FIELDS if field != "arm_id"}
    )


def _normalized_route(value: RouteArmSpec | Mapping[str, Any]) -> dict[str, Any]:
    payload = _route_payload(value)
    return {
        **payload,
        "route_identity_sha256": _route_identity_sha256(payload),
    }


def _validate_routes(
    route_arms: Any,
    *,
    require_wire_shape: bool,
) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(route_arms, (list, tuple))
        or len(route_arms) != CROSS_MODEL_ARM_COUNT
    ):
        raise CrossModelError(
            "matched-triad plans require exactly three route arms"
        )
    normalized = tuple(_normalized_route(route) for route in route_arms)
    if require_wire_shape:
        expected_fields = set(_ROUTE_FIELDS) | {"route_identity_sha256"}
        for supplied, expected in zip(route_arms, normalized, strict=True):
            if not isinstance(supplied, Mapping):
                raise CrossModelError("saved route arms must be objects")
            if set(supplied) != expected_fields or dict(supplied) != expected:
                raise CrossModelError("saved route arm differs from its canonical form")

    arm_ids = [route["arm_id"] for route in normalized]
    if len(set(arm_ids)) != CROSS_MODEL_ARM_COUNT:
        raise CrossModelError("matched-triad route arm ids must be unique")
    model_strata = [route["model_stratum"] for route in normalized]
    route_bindings = [route["route_binding_sha256"] for route in normalized]
    if (
        len(set(model_strata)) != CROSS_MODEL_ARM_COUNT
        or len(set(route_bindings)) != CROSS_MODEL_ARM_COUNT
    ):
        raise CrossModelError("matched-triad routes must be distinct")
    route_ids = [route["route_identity_sha256"] for route in normalized]
    if len(set(route_ids)) != CROSS_MODEL_ARM_COUNT:
        raise CrossModelError("matched-triad routes must be distinct")
    if tuple(arm_ids) != CROSS_MODEL_ARM_IDS:
        raise CrossModelError("matched-triad route arm ordering is frozen")
    for route in normalized:
        frozen = _ROUTE_FREEZES[str(route["arm_id"])]
        for field in (
            "model_stratum",
            "provider_profile",
            "request_model",
            "response_model",
            "sanitized_request_contract",
            "canary_artifact_sha256",
            "route_binding_sha256",
            "accepted_response_contract",
        ):
            if route[field] != frozen[field]:
                raise CrossModelError(
                    f"triad route {route['arm_id']} differs from frozen {field}"
                )
    return normalized  # type: ignore[return-value]


def _world_identity_sha256(world: Mapping[str, Any]) -> str:
    return _sha256_json(dict(world))


def _slot_identity_sha256(
    slot: Mapping[str, Any], *, world_identity_sha256: str
) -> str:
    return _sha256_json(
        {
            "world_identity_sha256": world_identity_sha256,
            "slot": dict(slot),
        }
    )


def _validate_public_grid(
    plan: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[Mapping[str, Any], ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    worlds = plan.get("worlds")
    slots = plan.get("slots")
    seeds = plan.get("world_seeds")
    if not isinstance(worlds, list) or not isinstance(slots, list) or not isinstance(seeds, list):
        raise CrossModelError("public worlds, world_seeds, and slots must be lists")
    if len(worlds) != CROSS_MODEL_WORLD_COUNT or len(seeds) != CROSS_MODEL_WORLD_COUNT:
        raise CrossModelError("matched-triad plan requires exactly 32 worlds")
    if len(slots) != CROSS_MODEL_CALLS_PER_ARM:
        raise CrossModelError("matched-triad plan requires exactly 96 slots")
    if any(type(seed) is not int for seed in seeds) or len(set(seeds)) != len(seeds):
        raise CrossModelError("public world seeds must be unique integers")

    world_digests: list[str] = []
    normalized_worlds: list[Mapping[str, Any]] = []
    for world_index, (seed, world) in enumerate(zip(seeds, worlds, strict=True)):
        if not isinstance(world, Mapping):
            raise CrossModelError("public world entries must be objects")
        if set(world) != _PUBLIC_WORLD_FIELDS:
            raise CrossModelError("public world entry uses a non-public schema")
        if world.get("world_index") != world_index or world.get("world_seed") != seed:
            raise CrossModelError("public world identity or ordering is malformed")
        if (
            not isinstance(world.get("D0"), list)
            or not isinstance(world.get("parent"), str)
            or not isinstance(world.get("parent_canonical_hash"), str)
            or not isinstance(world.get("allowed_paths"), list)
        ):
            raise CrossModelError("public world D0/parent identity is malformed")
        if any(
            not isinstance(row, Mapping)
            or set(row) != _PUBLIC_D0_ROW_FIELDS
            for row in world["D0"]
        ):
            raise CrossModelError("public world D0 rows use a non-public schema")
        if any(
            not isinstance(path, Mapping)
            or set(path) != _PUBLIC_ALLOWED_PATH_FIELDS
            for path in world["allowed_paths"]
        ):
            raise CrossModelError(
                "public world allowed paths use a non-public schema"
            )
        normalized_worlds.append(world)
        world_digests.append(_world_identity_sha256(world))

    normalized_slots: list[Mapping[str, Any]] = []
    slot_digests: list[str] = []
    seen_slot_ids: set[str] = set()
    stratum_counts: dict[str, int] = {}
    for serial_index, slot in enumerate(slots):
        if not isinstance(slot, Mapping):
            raise CrossModelError("public slot entries must be objects")
        if set(slot) != _PUBLIC_SLOT_FIELDS:
            raise CrossModelError("public slot entry uses a non-public schema")
        world_index = serial_index // CROSS_MODEL_CALLS_PER_WORLD
        slot_index = serial_index % CROSS_MODEL_CALLS_PER_WORLD + 1
        expected_seed = seeds[world_index]
        if (
            slot.get("serial_index") != serial_index
            or slot.get("world_index") != world_index
            or slot.get("world_seed") != expected_seed
            or slot.get("slot_index") != slot_index
            or slot.get("condition") != "motif"
        ):
            raise CrossModelError("public 32-world/96-slot layout is malformed")
        slot_id = slot.get("slot_id")
        if not isinstance(slot_id, str) or not slot_id or slot_id in seen_slot_ids:
            raise CrossModelError("public slot ids must be non-empty and unique")
        seen_slot_ids.add(slot_id)
        for field in ("motif_id", "motif_stratum", "motif"):
            if not isinstance(slot.get(field), str) or not slot.get(field):
                raise CrossModelError(f"public slot {field} is malformed")
        selection_digest = slot.get("motif_selection_sha256")
        if not isinstance(selection_digest, str) or not selection_digest:
            raise CrossModelError("public motif selection identity is malformed")
        stratum = str(slot["motif_stratum"])
        stratum_counts[stratum] = stratum_counts.get(stratum, 0) + 1
        normalized_slots.append(slot)
        slot_digests.append(
            _slot_identity_sha256(
                slot,
                world_identity_sha256=world_digests[world_index],
            )
        )

    expected_stratum_counts = {
        stratum: CROSS_MODEL_CALLS_PER_ARM // len(spark_closure.MOTIF_STRATA)
        for stratum in spark_closure.MOTIF_STRATA
    }
    if (
        stratum_counts != expected_stratum_counts
        or plan.get("stratum_counts") != expected_stratum_counts
    ):
        raise CrossModelError(
            "public plan must assign each frozen motif stratum exactly 24 slots"
        )
    expected_public_digest = _sha256_json(
        {
            "world_seeds": seeds,
            "worlds": worlds,
            "slots": slots,
        }
    )
    if plan.get("public_identity_sha256") != expected_public_digest:
        raise CrossModelError("shared public-plan identity digest mismatch")
    return (
        tuple(normalized_worlds),
        tuple(normalized_slots),
        tuple(world_digests),
        tuple(slot_digests),
    )


def _frozen_protocol() -> dict[str, Any]:
    return {
        "all_three_generations_then_shared_target_analysis_barrier": True,
        "route_arm_count": CROSS_MODEL_ARM_COUNT,
        "world_count": CROSS_MODEL_WORLD_COUNT,
        "calls_per_world_per_arm": CROSS_MODEL_CALLS_PER_WORLD,
        "expected_calls_per_arm": CROSS_MODEL_CALLS_PER_ARM,
        "expected_total_calls": CROSS_MODEL_ARM_COUNT * CROSS_MODEL_CALLS_PER_ARM,
        "neutral_calls_per_world": 0,
        "factual_calls_per_world": CROSS_MODEL_CALLS_PER_WORLD,
        "temperature": spark_closure.CLOSURE_TEMPERATURE,
        "max_output_tokens": spark_closure.CLOSURE_MAX_OUTPUT_TOKENS,
        "thinking": "disabled",
        "physical_attempts_per_model_slot": 1,
        "retry_supported": False,
        "resume_supported": False,
        "generation_reads_target_or_evidence_or_test_outcomes": False,
        "analysis_max_oracle_queries": spark_closure.CLOSURE_MAX_ROUNDS,
        "first_query": "generated_child_or_parent_control",
        "remaining_query_rule": "shortest_bank_member_then_canonical_hash",
        "primary_analysis_unit": "world",
        "pool_route_arms_as_independent_worlds": False,
        "endpoint_definitions": {
            "K1": "world_has_at_least_one_lineage_valid_factual_slot",
            "K2": (
                "same_K1_slot_is_non_direct_truth_retained_positive_nonmatch_"
                "and_reaches_four_round_singleton_full_domain_recovery"
            ),
            "K3": "same_K2_slot_and_frozen_parent_does_not_reach_endpoint",
            "K4": (
                "same_K3_slot_and_both_frozen_same_frame_matched_"
                "replacements_do_not_reach_endpoint"
            ),
        },
        "per_arm_classification": {
            "K4=0": "not_observed_under_frozen_protocol",
            "K4=1": "single_prospective_mechanism_instance_observed",
            "K4>=2": "prospective_cross_world_replication_observed",
        },
        "per_arm_interface_failure_flag": "K1=0",
        "execution_order_permutations": [
            list(order) for order in _TRIAD_ORDER_PERMUTATIONS
        ],
        "execution_order_rotation_unit": (
            "within_motif_stratum_occurrence_index_mod_6"
        ),
        "execution_slots_per_permutation": 16,
        "execution_slots_per_stratum_per_permutation": 4,
        "eight_cell_pattern_unit": "same_world_seed_within_each_K_layer",
        "pairwise_four_cell_unit": "same_world_seed_within_each_K_layer",
        "pairwise_route_pairs": [list(pair) for pair in _TRIAD_ROUTE_PAIRS],
        "joint_classification_closed_set": [
            "all_routes_replication_observed",
            "cross_family_replication_observed",
            "deepseek_family_only_replication_observed",
            "single_route_replication_observed",
            "replication_not_observed",
        ],
        "joint_classification_rules": {
            "all_three_arms_K4>=2": "all_routes_replication_observed",
            "glm_and_at_least_one_deepseek_arm_K4>=2": (
                "cross_family_replication_observed"
            ),
            "only_both_deepseek_arms_K4>=2": (
                "deepseek_family_only_replication_observed"
            ),
            "exactly_one_arm_K4>=2": "single_route_replication_observed",
            "no_arm_K4>=2": "replication_not_observed",
        },
        "single_instance_reporting_rule": (
            "per_arm_K4_equals_1_reported_separately_without_changing_"
            "cross_world_joint_classification"
        ),
    }


_TRIAD_ORDER_PERMUTATIONS = (
    (DEEPSEEK_FLASH_ARM_ID, DEEPSEEK_PRO_ARM_ID, GLM_ARM_ID),
    (DEEPSEEK_FLASH_ARM_ID, GLM_ARM_ID, DEEPSEEK_PRO_ARM_ID),
    (DEEPSEEK_PRO_ARM_ID, DEEPSEEK_FLASH_ARM_ID, GLM_ARM_ID),
    (DEEPSEEK_PRO_ARM_ID, GLM_ARM_ID, DEEPSEEK_FLASH_ARM_ID),
    (GLM_ARM_ID, DEEPSEEK_FLASH_ARM_ID, DEEPSEEK_PRO_ARM_ID),
    (GLM_ARM_ID, DEEPSEEK_PRO_ARM_ID, DEEPSEEK_FLASH_ARM_ID),
)
_TRIAD_ROUTE_PAIRS = (
    (DEEPSEEK_FLASH_ARM_ID, DEEPSEEK_PRO_ARM_ID),
    (DEEPSEEK_FLASH_ARM_ID, GLM_ARM_ID),
    (DEEPSEEK_PRO_ARM_ID, GLM_ARM_ID),
)


def _expected_execution_schedule(
    slots: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    occurrences_by_stratum: dict[str, int] = {}
    for slot in slots:
        serial_index = int(slot["serial_index"])
        motif_stratum = str(slot["motif_stratum"])
        stratum_occurrence_index = occurrences_by_stratum.get(motif_stratum, 0)
        order_permutation_index = stratum_occurrence_index % len(
            _TRIAD_ORDER_PERMUTATIONS
        )
        order = _TRIAD_ORDER_PERMUTATIONS[
            order_permutation_index
        ]
        occurrences_by_stratum[motif_stratum] = stratum_occurrence_index + 1
        for within_slot_order, arm_id in enumerate(order):
            schedule.append(
                {
                    "execution_index": len(schedule),
                    "serial_index": serial_index,
                    "slot_id": slot["slot_id"],
                    "motif_stratum": motif_stratum,
                    "stratum_occurrence_index": stratum_occurrence_index,
                    "order_permutation_index": order_permutation_index,
                    "arm_id": arm_id,
                    "within_slot_order": within_slot_order,
                }
            )
    return schedule


def _validate_plan(
    plan: Mapping[str, Any],
) -> tuple[
    tuple[Mapping[str, Any], ...],
    tuple[dict[str, Any], ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if not isinstance(plan, Mapping):
        raise CrossModelError("matched-triad plan must be an object")
    expected_plan_fields = {
        "schema_version",
        "kind",
        "protocol_id",
        "base_seed_namespace",
        "target_seed_namespace",
        "motif_selection_namespace",
        "evidence_scope",
        "source_manifest_sha256",
        "prior_layered_v1",
        "world_seeds",
        "worlds",
        "slots",
        "stratum_counts",
        "public_identity_sha256",
        "route_arms",
        "execution_schedule",
        "protocol",
        "plan_sha256",
    }
    if set(plan) != expected_plan_fields:
        raise CrossModelError("matched-triad plan uses a non-frozen schema")
    if (
        plan.get("schema_version") != 1
        or plan.get("kind") != "spark-cross-model-matched-triad-plan"
    ):
        raise CrossModelError("unsupported matched-triad plan schema")
    frozen_metadata = {
        "protocol_id": CROSS_MODEL_PROTOCOL_ID,
        "base_seed_namespace": CROSS_MODEL_SEED_NAMESPACE,
        "target_seed_namespace": CROSS_MODEL_TARGET_SEED_NAMESPACE,
        "motif_selection_namespace": CROSS_MODEL_MOTIF_SELECTION_NAMESPACE,
        "evidence_scope": CROSS_MODEL_EVIDENCE_SCOPE,
    }
    if any(plan.get(field) != value for field, value in frozen_metadata.items()):
        raise CrossModelError("triad plan protocol namespaces differ from the freeze")
    if not _is_lowercase_sha256(plan.get("source_manifest_sha256")):
        raise CrossModelError(
            "triad plan source_manifest_sha256 must be a lowercase SHA-256"
        )
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if plan.get("plan_sha256") != _sha256_json(unsigned):
        raise CrossModelError("matched-triad plan digest mismatch")

    routes = _validate_routes(plan.get("route_arms"), require_wire_shape=True)
    worlds, slots, world_digests, slot_digests = _validate_public_grid(plan)
    if tuple(plan.get("world_seeds", ())) != CROSS_MODEL_WORLD_SEEDS:
        raise CrossModelError("triad plan differs from the audited world seeds")
    if plan.get("prior_layered_v1") != LAYERED_V1_ARTIFACTS:
        raise CrossModelError("triad plan does not bind the layered-v1 artifacts")
    if plan.get("protocol") != _frozen_protocol():
        raise CrossModelError("matched-triad protocol fields are malformed")
    if plan.get("execution_schedule") != _expected_execution_schedule(slots):
        raise CrossModelError("balanced triad execution schedule is malformed")
    return slots, routes, world_digests, slot_digests


def _require_current_source_manifest(plan: Mapping[str, Any]) -> None:
    """Refuse live work when the target-blind plan binds another source tree."""

    current = source_manifest(PROJECT_ROOT).get("source_manifest_sha256")
    if plan.get("source_manifest_sha256") != current:
        raise CrossModelError(
            "triad plan source manifest drifted from the current implementation"
        )


def build_cross_model_plan(
    route_arms: Sequence[RouteArmSpec | Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the frozen target-free 32-world public plan.

    This constructs only the target-blind public bank/D0/parent view; it never
    derives a hidden target, enumerates a lineage, or runs a compressor.
    """

    routes = _validate_routes(tuple(route_arms), require_wire_shape=False)
    worlds = [
        spark_closure._target_free_public_world_entry(world_index, world_seed)
        for world_index, world_seed in enumerate(CROSS_MODEL_WORLD_SEEDS)
    ]
    slots: list[dict[str, Any]] = []
    stratum_counts = {stratum: 0 for stratum in spark_closure.MOTIF_STRATA}
    for world_index, world_seed in enumerate(CROSS_MODEL_WORLD_SEEDS):
        for factual_index in range(CROSS_MODEL_CALLS_PER_WORLD):
            serial_index = len(slots)
            slot_index = factual_index + 1
            stratum = spark_closure._stratum_for(
                world_index,
                factual_index,
                factual_calls_per_world=CROSS_MODEL_CALLS_PER_WORLD,
            )
            motif, selection_digest = spark_closure._select_motif(
                world_seed,
                slot_index,
                stratum,
                namespace=CROSS_MODEL_MOTIF_SELECTION_NAMESPACE,
            )
            stratum_counts[stratum] += 1
            slots.append(
                {
                    "serial_index": serial_index,
                    "slot_id": f"world-{world_seed}:motif-{slot_index}",
                    "world_index": world_index,
                    "world_seed": world_seed,
                    "slot_index": slot_index,
                    "condition": "motif",
                    "motif_id": motif.motif_id,
                    "motif_stratum": motif.stratum,
                    "motif": dsl.to_sexpr(motif.ast),
                    "motif_selection_sha256": selection_digest,
                }
            )

    public_identity_sha256 = _sha256_json(
        {
            "world_seeds": list(CROSS_MODEL_WORLD_SEEDS),
            "worlds": worlds,
            "slots": slots,
        }
    )

    plan_without_digest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "spark-cross-model-matched-triad-plan",
        "protocol_id": CROSS_MODEL_PROTOCOL_ID,
        "base_seed_namespace": CROSS_MODEL_SEED_NAMESPACE,
        "target_seed_namespace": CROSS_MODEL_TARGET_SEED_NAMESPACE,
        "motif_selection_namespace": CROSS_MODEL_MOTIF_SELECTION_NAMESPACE,
        "evidence_scope": CROSS_MODEL_EVIDENCE_SCOPE,
        "source_manifest_sha256": source_manifest(PROJECT_ROOT)[
            "source_manifest_sha256"
        ],
        "prior_layered_v1": dict(LAYERED_V1_ARTIFACTS),
        "world_seeds": list(CROSS_MODEL_WORLD_SEEDS),
        "worlds": worlds,
        "slots": slots,
        "stratum_counts": stratum_counts,
        "public_identity_sha256": public_identity_sha256,
        "route_arms": [dict(route) for route in routes],
        "execution_schedule": _expected_execution_schedule(slots),
        "protocol": _frozen_protocol(),
    }
    plan = {
        **plan_without_digest,
        "plan_sha256": _sha256_json(plan_without_digest),
    }
    _validate_plan(plan)
    return plan


def _route_by_arm_id(
    routes: Sequence[Mapping[str, Any]], arm_id: str
) -> Mapping[str, Any]:
    matches = [route for route in routes if route.get("arm_id") == arm_id]
    if len(matches) != 1:
        raise CrossModelError("arm_id does not identify exactly one planned route")
    return matches[0]


def _preflight_live_route(
    generator: OpenAICompatibleGenerator,
    route: Mapping[str, Any],
    *,
    max_output_tokens: int,
) -> AcceptedResponseContract:
    """Bind the concrete adapter and response contract before request one."""

    if type(generator) is not OpenAICompatibleGenerator:
        raise CrossModelError(
            "live route generation requires an OpenAICompatibleGenerator"
        )
    if generator.model != route["request_model"]:
        raise CrossModelError("runtime generator request model differs from its route")
    if generator.sanitized_request_contract() != route[
        "sanitized_request_contract"
    ]:
        raise CrossModelError("runtime generator request contract differs from its route")
    contract = _accepted_response_contract(route["accepted_response_contract"])
    if contract.provider_models[0] != route["response_model"]:
        raise CrossModelError("frozen response alias differs from its route")
    if contract.max_output_tokens != max_output_tokens:
        raise CrossModelError(
            "triad plan output cap differs from its route response contract"
        )
    try:
        observed_binding = route_binding_sha256(generator, contract)
    except (TypeError, ValueError) as exc:
        raise CrossModelError("runtime route binding could not be derived") from exc
    if observed_binding != route["route_binding_sha256"]:
        raise CrossModelError(
            "runtime generator/response contract drifted from the frozen route"
        )
    return contract


def _generate_record(
    plan: Mapping[str, Any],
    slot: Mapping[str, Any],
    *,
    slot_digest: str,
    world_digest: str,
    route: Mapping[str, Any],
    generator: OpenAICompatibleGenerator,
    contract: AcceptedResponseContract,
) -> dict[str, Any]:
    protocol = plan["protocol"]
    response = generator.generate(
        spark_closure.build_closure_prompt(plan, slot),
        temperature=float(protocol["temperature"]),
        max_output_tokens=int(protocol["max_output_tokens"]),
        round_index=int(slot["world_index"]),
        candidate_index=int(slot["slot_index"]),
    )
    try:
        response = spark_closure._validate_live_response(
            response,
            response_contract=contract,
        )
    except spark_closure.ClosureError as exc:
        raise CrossModelError(
            "provider response violates the route response contract"
        ) from exc
    if response.provider_model != route["response_model"]:
        raise CrossModelError("provider response alias differs from its route")
    try:
        parsed = spark_closure.parse_action(
            spark_closure._response_expression(response)
        )
    except spark_closure.ClosureError:
        parsed = None
    return {
        "serial_index": slot["serial_index"],
        "slot_id": slot["slot_id"],
        "world_index": slot["world_index"],
        "world_seed": slot["world_seed"],
        "slot_index": slot["slot_index"],
        "condition": slot["condition"],
        "motif_id": slot["motif_id"],
        "motif_stratum": slot["motif_stratum"],
        "world_identity_sha256": world_digest,
        "slot_identity_sha256": slot_digest,
        "action_parse_valid": parsed is not None,
        "action": None if parsed is None else parsed.to_dict(),
        "parse_failure": None if parsed is not None else "invalid_action_grammar",
        "telemetry": spark_closure._telemetry(response),
    }


def _seal_arm_generation(
    plan: Mapping[str, Any],
    route: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    triad_execution_schedule_validated: bool = False,
) -> dict[str, Any]:
    artifact_without_digest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "spark-cross-model-route-generation",
        "protocol_id": plan["protocol_id"],
        "arm_id": route["arm_id"],
        "route_arm": dict(route),
        "plan_sha256": plan["plan_sha256"],
        "public_identity_sha256": plan["public_identity_sha256"],
        "request_model": route["request_model"],
        "response_model": route["response_model"],
        "route_binding_sha256": route["route_binding_sha256"],
        "live_response_contract_validated": True,
        "triad_execution_schedule_validated": (
            triad_execution_schedule_validated
        ),
        "generation_complete_before_joint_target_analysis": True,
        "call_count": len(records),
        "records": [dict(record) for record in records],
    }
    return {
        **artifact_without_digest,
        "generation_sha256": _sha256_json(artifact_without_digest),
    }


def _partial_arm_generation(
    plan: Mapping[str, Any],
    route: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    partial_without_digest = {
        "schema_version": 1,
        "kind": "spark-cross-model-route-generation-partial",
        "protocol_id": plan["protocol_id"],
        "arm_id": route["arm_id"],
        "plan_sha256": plan["plan_sha256"],
        "route_binding_sha256": route["route_binding_sha256"],
        "generation_complete_before_joint_target_analysis": False,
        "resume_supported": False,
        "call_count": len(records),
        "expected_call_count": CROSS_MODEL_CALLS_PER_ARM,
        "records": [dict(record) for record in records],
    }
    return {
        **partial_without_digest,
        "partial_sha256": _sha256_json(partial_without_digest),
    }


def generate_cross_model_arm(
    plan: Mapping[str, Any],
    arm_id: str,
    generator: OpenAICompatibleGenerator,
    *,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Generate one arm sequentially for diagnostics and compatibility.

    Formal triad execution uses :func:`generate_cross_model` so all three arms
    follow the frozen balanced six-permutation schedule.
    """

    slots, routes, world_digests, slot_digests = _validate_plan(plan)
    _require_current_source_manifest(plan)
    route = _route_by_arm_id(routes, arm_id)
    protocol = plan["protocol"]
    contract = _preflight_live_route(
        generator,
        route,
        max_output_tokens=int(protocol["max_output_tokens"]),
    )
    records: list[dict[str, Any]] = []
    for slot, slot_digest in zip(slots, slot_digests, strict=True):
        records.append(
            _generate_record(
                plan,
                slot,
                slot_digest=slot_digest,
                world_digest=world_digests[int(slot["world_index"])],
                route=route,
                generator=generator,
                contract=contract,
            )
        )
        if progress_callback is not None:
            progress_callback(_partial_arm_generation(plan, route, records))
    return _seal_arm_generation(plan, route, records)


def _triad_generation_partial(
    plan: Mapping[str, Any],
    records_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    partial_without_digest = {
        "schema_version": 1,
        "kind": "spark-cross-model-matched-triad-generation-partial",
        "protocol_id": plan["protocol_id"],
        "plan_sha256": plan["plan_sha256"],
        "generation_complete_before_joint_target_analysis": False,
        "resume_supported": False,
        "executed_call_count": sum(len(rows) for rows in records_by_arm.values()),
        "expected_call_count": CROSS_MODEL_ARM_COUNT * CROSS_MODEL_CALLS_PER_ARM,
        "per_arm_call_count": {
            arm_id: len(records_by_arm[arm_id]) for arm_id in CROSS_MODEL_ARM_IDS
        },
        "records_by_arm": {
            arm_id: [dict(record) for record in records_by_arm[arm_id]]
            for arm_id in CROSS_MODEL_ARM_IDS
        },
    }
    return {
        **partial_without_digest,
        "partial_sha256": _sha256_json(partial_without_digest),
    }


def generate_cross_model(
    plan: Mapping[str, Any],
    generators: Mapping[str, OpenAICompatibleGenerator],
    *,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run all arms in the frozen balanced 288-call triad order."""

    slots, routes, world_digests, slot_digests = _validate_plan(plan)
    _require_current_source_manifest(plan)
    if not isinstance(generators, Mapping) or set(generators) != set(
        CROSS_MODEL_ARM_IDS
    ):
        raise CrossModelError(
            "matched-triad generation requires all three frozen generators"
        )
    route_by_arm = {str(route["arm_id"]): route for route in routes}
    contracts = {
        arm_id: _preflight_live_route(
            generators[arm_id],
            route_by_arm[arm_id],
            max_output_tokens=int(plan["protocol"]["max_output_tokens"]),
        )
        for arm_id in CROSS_MODEL_ARM_IDS
    }
    records_by_arm: dict[str, list[dict[str, Any]]] = {
        arm_id: [] for arm_id in CROSS_MODEL_ARM_IDS
    }
    for scheduled in plan["execution_schedule"]:
        arm_id = str(scheduled["arm_id"])
        serial_index = int(scheduled["serial_index"])
        slot = slots[serial_index]
        records_by_arm[arm_id].append(
            _generate_record(
                plan,
                slot,
                slot_digest=slot_digests[serial_index],
                world_digest=world_digests[int(slot["world_index"])],
                route=route_by_arm[arm_id],
                generator=generators[arm_id],
                contract=contracts[arm_id],
            )
        )
        if progress_callback is not None:
            progress_callback(_triad_generation_partial(plan, records_by_arm))
    generations = [
        _seal_arm_generation(
            plan,
            route_by_arm[arm_id],
            sorted(records_by_arm[arm_id], key=lambda row: int(row["serial_index"])),
            triad_execution_schedule_validated=True,
        )
        for arm_id in CROSS_MODEL_ARM_IDS
    ]
    bundle_without_digest = {
        "schema_version": 1,
        "kind": "spark-cross-model-matched-triad-generations",
        "protocol_id": plan["protocol_id"],
        "plan_sha256": plan["plan_sha256"],
        "execution_schedule_completed": True,
        "execution_trace": [dict(row) for row in plan["execution_schedule"]],
        "total_call_count": CROSS_MODEL_ARM_COUNT * CROSS_MODEL_CALLS_PER_ARM,
        "generations": generations,
    }
    return {
        **bundle_without_digest,
        "bundle_sha256": _sha256_json(bundle_without_digest),
    }


def _validate_arm_generation(
    plan: Mapping[str, Any],
    generation: Mapping[str, Any],
    *,
    expected_route: Mapping[str, Any],
    slots: Sequence[Mapping[str, Any]],
    world_digests: Sequence[str],
    slot_digests: Sequence[str],
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(generation, Mapping):
        raise CrossModelError("route generation artifacts must be objects")
    if (
        generation.get("schema_version") != 1
        or generation.get("kind") != "spark-cross-model-route-generation"
    ):
        raise CrossModelError("unsupported route generation artifact schema")
    if generation.get("protocol_id") != plan.get("protocol_id"):
        raise CrossModelError("route generation protocol differs from its plan")
    if generation.get("arm_id") != expected_route.get("arm_id"):
        raise CrossModelError("route generation arm differs from its planned route")
    if generation.get("route_arm") != expected_route:
        raise CrossModelError("route generation binding differs from its planned route")
    if generation.get("plan_sha256") != plan.get("plan_sha256"):
        raise CrossModelError("route generation belongs to another triad plan")
    if generation.get("public_identity_sha256") != plan.get(
        "public_identity_sha256"
    ):
        raise CrossModelError("route generation public identity differs from its plan")
    if (
        generation.get("live_response_contract_validated") is not True
        or generation.get("triad_execution_schedule_validated") is not True
        or generation.get("request_model") != expected_route.get("request_model")
        or generation.get("response_model") != expected_route.get("response_model")
        or generation.get("route_binding_sha256")
        != expected_route.get("route_binding_sha256")
    ):
        raise CrossModelError(
            "route generation lacks its validated live response contract binding"
        )
    unsigned = {
        key: value for key, value in generation.items() if key != "generation_sha256"
    }
    if generation.get("generation_sha256") != _sha256_json(unsigned):
        raise CrossModelError("route generation artifact digest mismatch")
    records = generation.get("records")
    if (
        generation.get("generation_complete_before_joint_target_analysis") is not True
        or generation.get("call_count") != CROSS_MODEL_CALLS_PER_ARM
        or not isinstance(records, list)
        or len(records) != CROSS_MODEL_CALLS_PER_ARM
    ):
        raise CrossModelError("each route arm must contain exactly 96 complete records")

    normalized: list[Mapping[str, Any]] = []
    for serial_index, (slot, record) in enumerate(zip(slots, records, strict=True)):
        if not isinstance(record, Mapping):
            raise CrossModelError("route generation records must be objects")
        expected_identity = {
            "serial_index": slot["serial_index"],
            "slot_id": slot["slot_id"],
            "world_index": slot["world_index"],
            "world_seed": slot["world_seed"],
            "slot_index": slot["slot_index"],
            "condition": slot["condition"],
            "motif_id": slot["motif_id"],
            "motif_stratum": slot["motif_stratum"],
            "world_identity_sha256": world_digests[int(slot["world_index"])],
            "slot_identity_sha256": slot_digests[serial_index],
        }
        if any(
            record.get(field) != expected_identity[field]
            for field in _RECORD_IDENTITY_FIELDS
        ):
            raise CrossModelError(
                "route generation world/D0/parent/motif identity drifted"
            )
        parse_valid = record.get("action_parse_valid")
        if type(parse_valid) is not bool:
            raise CrossModelError("route generation parse validity must be boolean")
        try:
            parsed = spark_closure._parsed_action_fields(record.get("action"))
        except spark_closure.ClosureError as exc:
            raise CrossModelError("saved route action fields are malformed") from exc
        if parse_valid != (parsed is not None):
            raise CrossModelError("route generation parse status disagrees with action")
        if parse_valid and record.get("parse_failure") is not None:
            raise CrossModelError("valid route action carries a parse failure")
        if not parse_valid and record.get("parse_failure") != "invalid_action_grammar":
            raise CrossModelError("invalid route action has an unknown parse failure")
        telemetry = record.get("telemetry")
        if not isinstance(telemetry, Mapping):
            raise CrossModelError("route generation telemetry must be an object")
        if telemetry.get("provider_model") != expected_route["response_model"]:
            raise CrossModelError(
                "route generation response alias differs from its route"
            )
        normalized.append(record)
    return tuple(normalized)


def validate_joint_generation_barrier(
    plan: Mapping[str, Any],
    generations: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Validate all three complete arms using public data only.

    This function is the single gate in front of target-dependent analysis.
    It neither accepts a partial arm nor combines records from different plans,
    routes, worlds, parents, D0 observations, or assigned motifs.
    """

    slots, routes, world_digests, slot_digests = _validate_plan(plan)
    _require_current_source_manifest(plan)
    if (
        not isinstance(generations, (list, tuple))
        or len(generations) != CROSS_MODEL_ARM_COUNT
    ):
        raise CrossModelError(
            "joint analysis requires all three route generation arms"
        )
    arm_ids = [
        generation.get("arm_id") if isinstance(generation, Mapping) else None
        for generation in generations
    ]
    expected_arm_ids = [route["arm_id"] for route in routes]
    if len(set(arm_ids)) != CROSS_MODEL_ARM_COUNT:
        raise CrossModelError("joint analysis received a duplicate route arm")
    if set(arm_ids) != set(expected_arm_ids):
        raise CrossModelError("joint analysis route arms differ from its triad plan")

    artifacts_by_arm: dict[str, Mapping[str, Any]] = {}
    identities_by_arm: dict[str, tuple[tuple[Any, ...], ...]] = {}
    for route in routes:
        arm_id = str(route["arm_id"])
        generation = next(
            item for item in generations if item.get("arm_id") == arm_id
        )
        records = _validate_arm_generation(
            plan,
            generation,
            expected_route=route,
            slots=slots,
            world_digests=world_digests,
            slot_digests=slot_digests,
        )
        identities_by_arm[arm_id] = tuple(
            tuple(record[field] for field in _RECORD_IDENTITY_FIELDS)
            for record in records
        )
        artifacts_by_arm[arm_id] = generation

    first_identity = identities_by_arm[expected_arm_ids[0]]
    if any(
        identities_by_arm[arm_id] != first_identity
        for arm_id in expected_arm_ids[1:]
    ):
        raise CrossModelError(
            "triad arms do not share world/D0/parent/motif identity"
        )
    return artifacts_by_arm


def _classify_arm(world_counts_k: Mapping[str, Any]) -> str:
    try:
        k4 = int(world_counts_k["K4"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CrossModelError("arm K1-K4 counts are malformed") from exc
    return spark_closure.classify_layered_outcome(k4)


def _joint_classification(
    counts_by_arm: Mapping[str, Mapping[str, Any]],
) -> str:
    if not isinstance(counts_by_arm, Mapping) or set(counts_by_arm) != set(
        CROSS_MODEL_ARM_IDS
    ):
        raise CrossModelError("joint classification requires all triad arm counts")
    try:
        positive = {
            arm_id
            for arm_id in CROSS_MODEL_ARM_IDS
            if int(counts_by_arm[arm_id]["K4"]) >= 2
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise CrossModelError("triad K4 counts are malformed") from exc
    if positive == set(CROSS_MODEL_ARM_IDS):
        return "all_routes_replication_observed"
    if GLM_ARM_ID in positive and positive.intersection(
        {DEEPSEEK_FLASH_ARM_ID, DEEPSEEK_PRO_ARM_ID}
    ):
        return "cross_family_replication_observed"
    if positive == {DEEPSEEK_FLASH_ARM_ID, DEEPSEEK_PRO_ARM_ID}:
        return "deepseek_family_only_replication_observed"
    if len(positive) == 1:
        return "single_route_replication_observed"
    return "replication_not_observed"


_EIGHT_CELL_PATTERN_ARMS = {
    "all_three": CROSS_MODEL_ARM_IDS,
    "deepseek_flash_and_pro_only": (
        DEEPSEEK_FLASH_ARM_ID,
        DEEPSEEK_PRO_ARM_ID,
    ),
    "deepseek_flash_and_glm_only": (DEEPSEEK_FLASH_ARM_ID, GLM_ARM_ID),
    "deepseek_pro_and_glm_only": (DEEPSEEK_PRO_ARM_ID, GLM_ARM_ID),
    "deepseek_flash_only": (DEEPSEEK_FLASH_ARM_ID,),
    "deepseek_pro_only": (DEEPSEEK_PRO_ARM_ID,),
    "glm_only": (GLM_ARM_ID,),
    "none": (),
}


def _triad_endpoint_tables(
    worlds_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Build eight-cell and all three pairwise tables for every K layer."""

    if not isinstance(worlds_by_arm, Mapping) or set(worlds_by_arm) != set(
        CROSS_MODEL_ARM_IDS
    ):
        raise CrossModelError("endpoint tables require all triad arms")
    indexed: dict[str, dict[Any, Mapping[str, Any]]] = {}
    ordered_seeds: list[Any] | None = None
    for arm_id in CROSS_MODEL_ARM_IDS:
        worlds = worlds_by_arm[arm_id]
        by_seed = {row.get("world_seed"): row for row in worlds}
        arm_seeds = [row.get("world_seed") for row in worlds]
        if len(by_seed) != len(worlds):
            raise CrossModelError("triad endpoint worlds are duplicated")
        if ordered_seeds is None:
            ordered_seeds = arm_seeds
        elif arm_seeds != ordered_seeds:
            raise CrossModelError(
                "triad endpoint worlds are missing, reordered, or mismatched"
            )
        indexed[arm_id] = by_seed
    assert ordered_seeds is not None

    pattern_name_by_arms = {
        frozenset(arm_ids): name
        for name, arm_ids in _EIGHT_CELL_PATTERN_ARMS.items()
    }
    endpoint_names = {"K1": "L", "K2": "M", "K3": "D", "K4": "R"}
    tables: dict[str, Any] = {}
    for alias, endpoint in endpoint_names.items():
        pattern_seeds: dict[str, list[Any]] = {
            name: [] for name in _EIGHT_CELL_PATTERN_ARMS
        }
        for seed in ordered_seeds:
            passed: set[str] = set()
            for arm_id in CROSS_MODEL_ARM_IDS:
                endpoints = indexed[arm_id][seed].get("endpoints")
                if not isinstance(endpoints, Mapping):
                    raise CrossModelError("triad world endpoints are malformed")
                if endpoints.get(endpoint) is True:
                    passed.add(arm_id)
            pattern_seeds[pattern_name_by_arms[frozenset(passed)]].append(seed)

        pairwise: dict[str, Any] = {}
        for left_arm, right_arm in _TRIAD_ROUTE_PAIRS:
            cells: dict[str, list[Any]] = {
                "both": [],
                "left_only": [],
                "right_only": [],
                "neither": [],
            }
            for seed in ordered_seeds:
                left_endpoints = indexed[left_arm][seed].get("endpoints")
                right_endpoints = indexed[right_arm][seed].get("endpoints")
                assert isinstance(left_endpoints, Mapping)
                assert isinstance(right_endpoints, Mapping)
                left_passed = left_endpoints.get(endpoint) is True
                right_passed = right_endpoints.get(endpoint) is True
                cell = (
                    "both"
                    if left_passed and right_passed
                    else "left_only"
                    if left_passed
                    else "right_only"
                    if right_passed
                    else "neither"
                )
                cells[cell].append(seed)
            pair_key = f"{left_arm}__{right_arm}"
            pairwise[pair_key] = {
                "left_arm_id": left_arm,
                "right_arm_id": right_arm,
                "world_denominator": len(ordered_seeds),
                "counts": {
                    cell: len(world_seeds)
                    for cell, world_seeds in cells.items()
                },
                "world_seeds": cells,
            }
        tables[alias] = {
            "endpoint": endpoint,
            "world_denominator": len(ordered_seeds),
            "eight_cell_pattern_arm_ids": {
                name: list(arm_ids)
                for name, arm_ids in _EIGHT_CELL_PATTERN_ARMS.items()
            },
            "eight_cell_counts": {
                name: len(world_seeds)
                for name, world_seeds in pattern_seeds.items()
            },
            "eight_cell_world_seeds": pattern_seeds,
            "pairwise_four_cell_tables": pairwise,
        }
    return tables


def _run_joint_analysis_core(
    plan: Mapping[str, Any],
    artifacts_by_arm: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Fixed target-dependent core, called only after the joint barrier."""

    slots = tuple(plan["slots"])
    parsed_by_arm: dict[str, dict[int, Any]] = {}
    for arm_id in CROSS_MODEL_ARM_IDS:
        parsed_by_arm[arm_id] = {
            int(record["serial_index"]): spark_closure._parsed_action_fields(
                record.get("action")
            )
            for record in artifacts_by_arm[arm_id]["records"]
        }

    results_by_arm: dict[str, list[dict[str, Any]]] = {
        arm_id: [] for arm_id in CROSS_MODEL_ARM_IDS
    }
    shared_worlds: list[dict[str, Any]] = []
    target_namespace = str(plan["target_seed_namespace"])
    for plan_world in plan["worlds"]:
        world_seed = int(plan_world["world_seed"])
        target_seed = spark_closure._target_seed_for_namespace(
            world_seed,
            target_namespace,
        )
        world = spark_closure.generate_spark_world(
            world_seed,
            target_seed=target_seed,
        )
        parent = spark_closure.select_parent(world)
        if (
            dsl.canonical_hash(parent) != plan_world["parent_canonical_hash"]
            or dsl.to_sexpr(parent) != plan_world["parent"]
        ):
            raise CrossModelError("public parent differs from the shared target world")
        actual_d0 = [
            {"point": list(example.point), "label": example.label}
            for example in world.train
        ]
        if actual_d0 != plan_world["D0"]:
            raise CrossModelError("public D0 differs from the shared target world")
        compressor = spark_closure.SparkCompressor(world)
        parent_result = compressor.run(
            parent,
            max_rounds=spark_closure.CLOSURE_MAX_ROUNDS,
        )
        lineages = spark_closure.enumerate_reachable_children(world)
        world_slots = [
            slot for slot in slots if int(slot["world_seed"]) == world_seed
        ]
        shared = {
            "world_seed": world_seed,
            "target_seed_namespace_sha256": spark_closure._target_seed_digest(
                world_seed,
                namespace=target_namespace,
            ),
            "world_hash": world.world_hash,
            "target_index": world.target_index,
            "target_canonical_hash": dsl.canonical_hash(world.target),
            "parent_canonical_hash": dsl.canonical_hash(parent),
        }
        shared_worlds.append(shared)
        for arm_id in CROSS_MODEL_ARM_IDS:
            rows = [
                spark_closure._analyze_factual_slot(
                    slot=slot,
                    parsed=parsed_by_arm[arm_id][int(slot["serial_index"])],
                    plan_world=plan_world,
                    world=world,
                    compressor=compressor,
                    parent=parent,
                    parent_result=parent_result,
                    lineages=lineages,
                )
                for slot in world_slots
            ]
            results_by_arm[arm_id].append(
                {
                    **shared,
                    "slot_results": rows,
                    "strict_event_count": sum(
                        bool(row.get("strict_event")) for row in rows
                    ),
                }
            )

    route_by_arm = {
        str(route["arm_id"]): route for route in plan["route_arms"]
    }
    summaries = {
        arm_id: spark_closure.summarize_layered_endpoints(
            results_by_arm[arm_id]
        )
        for arm_id in CROSS_MODEL_ARM_IDS
    }
    arms: dict[str, Any] = {}
    for arm_id in CROSS_MODEL_ARM_IDS:
        summary = summaries[arm_id]
        counts = summary["world_counts_K"]
        by_seed = {row["world_seed"]: row for row in summary["worlds"]}
        for world_row in results_by_arm[arm_id]:
            endpoint_row = by_seed[world_row["world_seed"]]
            world_row["layered_endpoints"] = endpoint_row["endpoints"]
            world_row["layered_qualifying_slot_ids"] = endpoint_row[
                "qualifying_slot_ids"
            ]
            world_row["weak_at_least_one_replacement_failure"] = endpoint_row[
                "weak_at_least_one_replacement_failure"
            ]
        arms[arm_id] = {
            "route_arm": dict(route_by_arm[arm_id]),
            "model_call_count": len(artifacts_by_arm[arm_id]["records"]),
            "world_denominator": len(plan["worlds"]),
            "world_counts_K": dict(counts),
            "classification": _classify_arm(counts),
            "model_dsl_interface_failure": int(counts["K1"]) == 0,
            "layered_summary": {
                key: value
                for key, value in summary.items()
                if key not in {"worlds", "classification"}
            },
            "worlds": results_by_arm[arm_id],
        }

    endpoint_tables = _triad_endpoint_tables(
        {
            arm_id: summaries[arm_id]["worlds"]
            for arm_id in CROSS_MODEL_ARM_IDS
        }
    )
    counts_by_arm = {
        arm_id: summaries[arm_id]["world_counts_K"]
        for arm_id in CROSS_MODEL_ARM_IDS
    }
    return {
        "shared_target_world_count": len(shared_worlds),
        "shared_worlds": shared_worlds,
        "arms": arms,
        "triad_endpoint_world_tables": endpoint_tables,
        "pooled_route_arm_world_analysis_performed": False,
        "slot_results_treated_as_iid": False,
        "joint_interface_failure_arms": [
            arm_id
            for arm_id in CROSS_MODEL_ARM_IDS
            if int(summaries[arm_id]["world_counts_K"]["K1"]) == 0
        ],
        "single_prospective_mechanism_instance_arms": [
            arm_id
            for arm_id in CROSS_MODEL_ARM_IDS
            if int(summaries[arm_id]["world_counts_K"]["K4"]) == 1
        ],
        "cross_world_replication_arms": [
            arm_id
            for arm_id in CROSS_MODEL_ARM_IDS
            if int(summaries[arm_id]["world_counts_K"]["K4"]) >= 2
        ],
        "joint_classification": _joint_classification(counts_by_arm),
        "interpretation_limit": (
            "matched-triad finite-system cross-model mechanism replication only; not "
            "temperature causation, prevalence, an average treatment effect, or "
            "evidence of human-unknown discovery"
        ),
    }


def analyze_cross_model(
    plan: Mapping[str, Any],
    bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the fixed core only from the frozen matched-triad bundle."""

    generations = _generations_from_bundle(plan, bundle)
    artifacts_by_arm = validate_joint_generation_barrier(plan, generations)
    core_result = _run_joint_analysis_core(plan, artifacts_by_arm)

    report_without_digest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "spark-cross-model-matched-triad-joint-analysis",
        "protocol_id": plan["protocol_id"],
        "plan_sha256": plan["plan_sha256"],
        "public_identity_sha256": plan["public_identity_sha256"],
        "all_three_96_record_arms_validated_before_analysis": True,
        "balanced_288_call_execution_schedule_validated": True,
        "generation_bundle_sha256": bundle["bundle_sha256"],
        "generation_sha256_by_arm": {
            arm_id: artifact["generation_sha256"]
            for arm_id, artifact in artifacts_by_arm.items()
        },
        "joint_analysis": core_result,
    }
    return {
        **report_without_digest,
        "analysis_sha256": _sha256_json(report_without_digest),
    }


def _read_json(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CrossModelError(f"cannot read JSON artifact {source}") from exc
    if not isinstance(value, dict):
        raise CrossModelError(f"JSON artifact {source} must contain an object")
    return value


def _emit_json_exclusive(value: Mapping[str, Any], output: str | Path) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise CrossModelError(f"refusing to overwrite artifact {path}") from exc


def _emit_progress(value: Mapping[str, Any], output: str | Path) -> None:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(rendered)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _generations_from_bundle(
    plan: Mapping[str, Any], bundle: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    if (
        not isinstance(bundle, Mapping)
        or bundle.get("schema_version") != 1
        or bundle.get("kind") != "spark-cross-model-matched-triad-generations"
        or bundle.get("protocol_id") != CROSS_MODEL_PROTOCOL_ID
        or bundle.get("plan_sha256") != plan.get("plan_sha256")
        or bundle.get("execution_schedule_completed") is not True
        or bundle.get("execution_trace") != plan.get("execution_schedule")
        or bundle.get("total_call_count")
        != CROSS_MODEL_ARM_COUNT * CROSS_MODEL_CALLS_PER_ARM
    ):
        raise CrossModelError("matched-triad generation bundle is malformed")
    unsigned = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if bundle.get("bundle_sha256") != _sha256_json(unsigned):
        raise CrossModelError("matched-triad generation bundle digest mismatch")
    generations = bundle.get("generations")
    if (
        not isinstance(generations, list)
        or len(generations) != CROSS_MODEL_ARM_COUNT
    ):
        raise CrossModelError(
            "matched-triad generation bundle must contain three arms"
        )
    return tuple(generations)  # type: ignore[return-value]


def _load_cli_routes_and_credentials(
    args: argparse.Namespace,
) -> tuple[
    tuple[RouteArmSpec, ...],
    dict[str, ProviderCredentials],
]:
    deepseek = load_provider_credentials(
        prefix=args.deepseek_env_prefix,
        env_file=args.deepseek_env_file,
    )
    if deepseek.model != _ROUTE_FREEZES[DEEPSEEK_FLASH_ARM_ID]["request_model"]:
        raise CrossModelError(
            "shared DeepSeek credentials must name the frozen Flash model"
        )
    glm = load_provider_credentials(
        prefix=args.glm_env_prefix,
        env_file=args.glm_env_file,
    )
    credentials = {
        DEEPSEEK_FLASH_ARM_ID: deepseek,
        DEEPSEEK_PRO_ARM_ID: ProviderCredentials(
            base_url=deepseek.base_url,
            model=str(_ROUTE_FREEZES[DEEPSEEK_PRO_ARM_ID]["request_model"]),
            api_key=deepseek.api_key,
        ),
        GLM_ARM_ID: glm,
    }
    routes = tuple(
        route_arm_from_canary(arm_id, credentials[arm_id])
        for arm_id in CROSS_MODEL_ARM_IDS
    )
    return routes, credentials


def _add_credential_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--deepseek-env-file",
        "--reference-env-file",
        dest="deepseek_env_file",
        type=Path,
    )
    parser.add_argument(
        "--glm-env-file",
        "--comparison-env-file",
        dest="glm_env_file",
        type=Path,
    )
    parser.add_argument(
        "--deepseek-env-prefix",
        "--reference-env-prefix",
        dest="deepseek_env_prefix",
        default="DEEPSEEK",
    )
    parser.add_argument(
        "--glm-env-prefix",
        "--comparison-env-prefix",
        dest="glm_env_prefix",
        default="TENCENT",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Frozen research-only matched-triad closure runner"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan")
    _add_credential_arguments(plan_parser)
    plan_parser.add_argument("--output", type=Path, required=True)

    generate_parser = subparsers.add_parser("generate")
    _add_credential_arguments(generate_parser)
    generate_parser.add_argument("--plan", type=Path, required=True)
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--progress", type=Path)
    generate_parser.add_argument("--execute", action="store_true")

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--plan", type=Path, required=True)
    analyze_parser.add_argument("--generations", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "plan":
        routes, _credentials = _load_cli_routes_and_credentials(args)
        plan = build_cross_model_plan(routes)
        _emit_json_exclusive(plan, args.output)
        return 0
    if args.command == "generate":
        if not args.execute:
            parser.error("generate requires --execute before any provider request")
        plan = _read_json(args.plan)
        _validate_plan(plan)
        _require_current_source_manifest(plan)
        routes, credentials = _load_cli_routes_and_credentials(args)
        if [route.to_dict() for route in routes] != [
            {key: value for key, value in saved.items() if key != "route_identity_sha256"}
            for saved in plan["route_arms"]
        ]:
            raise CrossModelError("current canary routes differ from the triad plan")
        generators = {
            arm_id: build_v3_generator(credentials[arm_id])
            for arm_id in CROSS_MODEL_ARM_IDS
        }
        callback = (
            None
            if args.progress is None
            else lambda partial: _emit_progress(partial, args.progress)
        )
        bundle = generate_cross_model(
            plan,
            generators,
            progress_callback=callback,
        )
        _emit_json_exclusive(bundle, args.output)
        return 0
    if args.command == "analyze":
        plan = _read_json(args.plan)
        bundle = _read_json(args.generations)
        report = analyze_cross_model(plan, bundle)
        _emit_json_exclusive(report, args.output)
        return 0
    raise AssertionError("unreachable cross-model command")


# Compatibility aliases for notebooks written against the retired paired API.
PairedCrossModelError = CrossModelError
build_paired_cross_model_plan = build_cross_model_plan
generate_route_arm = generate_cross_model_arm
analyze_paired_cross_model = analyze_cross_model


__all__ = [
    "CROSS_MODEL_ARM_COUNT",
    "CROSS_MODEL_ARM_IDS",
    "CROSS_MODEL_CALLS_PER_ARM",
    "CROSS_MODEL_CALLS_PER_WORLD",
    "CROSS_MODEL_PROTOCOL_ID",
    "CROSS_MODEL_SEED_NAMESPACE",
    "CROSS_MODEL_TARGET_SEED_NAMESPACE",
    "CROSS_MODEL_MOTIF_SELECTION_NAMESPACE",
    "CROSS_MODEL_WORLD_SEEDS",
    "CROSS_MODEL_WORLD_COUNT",
    "DEEPSEEK_FLASH_ARM_ID",
    "DEEPSEEK_PRO_ARM_ID",
    "GLM_ARM_ID",
    "REFERENCE_ARM_ID",
    "COMPARISON_ARM_ID",
    "CrossModelError",
    "CrossModelGenerator",
    "PairedCrossModelError",
    "RouteArmSpec",
    "analyze_cross_model",
    "analyze_paired_cross_model",
    "build_cross_model_plan",
    "build_paired_cross_model_plan",
    "generate_cross_model",
    "generate_cross_model_arm",
    "generate_route_arm",
    "main",
    "route_arm_from_canary",
    "validate_joint_generation_barrier",
]


if __name__ == "__main__":  # pragma: no cover - exercised through main tests
    raise SystemExit(main())
