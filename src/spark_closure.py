"""Exploratory 24-call spark-to-knowledge mechanism closure.

This module implements the post-calibration amendment recorded in
``spark-to-knowledge-experiment-plan.md``.  Generation and analysis are two
separate stages: all 24 model outputs are reduced to action fields and sealed
before any target-dependent oracle or private/full-domain outcome is built.

The code is intentionally a small research runner.  It provides no retry or
resume machinery and never sends a network request unless the CLI ``generate``
subcommand is explicitly invoked with ``--execute``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from . import dsl
from .credentials import ProviderCredentials, load_provider_credentials
from .providers.openai_compatible import OpenAICompatibleGenerator
from .provenance import PROJECT_ROOT, source_manifest
from .runner import GenerationResponse
from .spark_compressor import CompressionResult, SparkCompressor
from .spark_lineage import (
    EDIT_PATHS,
    MOTIF_STRATA,
    WRAP_OPERATORS,
    EditAction,
    LineageRecord,
    build_motif_library,
    enumerate_reachable_children,
    get_subtree,
    motif_by_id,
    select_parent,
)
from .spark_world import SparkWorld, generate_spark_world
from .staged_pilot_v3 import AcceptedResponseContract, V3ResponseContractError
from .v3_live import build_v3_generator, model_binding_from_canary


CLOSURE_WORLD_SEEDS = tuple(range(3000, 3006))
CLOSURE_CALLS_PER_WORLD = 4
CLOSURE_FACTUAL_CALLS_PER_WORLD = 3
CLOSURE_EXPECTED_CALLS = 24
CLOSURE_EXPECTED_FACTUAL_CALLS = 18
CLOSURE_TEMPERATURE = 0.2
CLOSURE_MAX_OUTPUT_TOKENS = 256
CLOSURE_MAX_ROUNDS = 4
CLOSURE_PROTOCOL_ID = "development-v1"
CLOSURE_SEED_NAMESPACE = "spark-closure-v1"
CLOSURE_TARGET_SEED_NAMESPACE = CLOSURE_SEED_NAMESPACE
CLOSURE_MOTIF_SELECTION_NAMESPACE = CLOSURE_SEED_NAMESPACE
CLOSURE_EVIDENCE_SCOPE = "post_calibration_exploratory_only"
PROSPECTIVE_PROTOCOL_ID = "prospective-v1"
PROSPECTIVE_WORLD_SEEDS = tuple(range(10000, 10006))
PROSPECTIVE_TARGET_SEED_NAMESPACE = "spark-closure-prospective-v1"
PROSPECTIVE_MOTIF_SELECTION_NAMESPACE = CLOSURE_MOTIF_SELECTION_NAMESPACE
PROSPECTIVE_EVIDENCE_SCOPE = "prospective_mechanism_replication"
PROSPECTIVE_V2_PROTOCOL_ID = "prospective-v2"
PROSPECTIVE_V2_WORLD_SEEDS = tuple(range(10010, 10016))
PROSPECTIVE_V2_TARGET_SEED_NAMESPACE = "spark-closure-prospective-v2"
PROSPECTIVE_V2_MOTIF_SELECTION_NAMESPACE = CLOSURE_MOTIF_SELECTION_NAMESPACE
PROSPECTIVE_V2_EVIDENCE_SCOPE = PROSPECTIVE_EVIDENCE_SCOPE
LAYERED_PROTOCOL_ID = "layered-v1"
LAYERED_WORLD_SEEDS = (
    149164194557103187,
    197785174046540536,
    8689498207041883831,
    7372109617068943611,
    1788933733710549810,
    5850954761208054067,
    8468748721542519872,
    508095208076430127,
    7255759396679503842,
    3010699749877793097,
    2473712061732812970,
    856738614459882241,
    5200387050906735940,
    6971971984972950855,
    8004701421764506100,
    329962133897780649,
    3073125064765817691,
    487714150649552500,
    5527731908070175319,
    6466267340987574428,
    7352683128229967339,
    8557001049290273476,
    3944888237210388916,
    2480113330417097531,
    3084195352423810677,
    9194213173342005834,
    6760555811078959657,
    4235194283738692887,
    3092150612108050083,
    1143034887637611591,
    3472459390724036822,
    2782549438481220964,
)
LAYERED_TARGET_SEED_NAMESPACE = "spark-closure-layered-v1"
LAYERED_MOTIF_SELECTION_NAMESPACE = "spark-closure-layered-v1"
LAYERED_EVIDENCE_SCOPE = "prospective_layered_mechanism_followup"
LAYERED_CALLS_PER_WORLD = 3
LAYERED_FACTUAL_CALLS_PER_WORLD = 3
LAYERED_EXPECTED_CALLS = 96
LAYERED_EXPECTED_FACTUAL_CALLS = 96
LAYERED_CANARY_SHA256 = (
    "d5a4df862aa4084c34af2e76da3ae98985c7f3c63fbc8cc3bdfe1edfb4edc497"
)
LAYERED_PRIOR_PROSPECTIVE_V2 = {
    "protocol_id": PROSPECTIVE_V2_PROTOCOL_ID,
    "plan_sha256": "dd7b70faab2960873bdd727f6424bea3e219d6cc341035d0632e9493fa5f8612",
    "generation_sha256": "908c445e749920b8ba2333508f4c2f1cdb41aee197563458069e77ee1111c15e",
    "analysis_sha256": "1d5eab64cd1540ab872b5bbecef0d53d0eb3219e77ddc5069e33cf71ac728390",
}
PROSPECTIVE_REPLICATION_OF = {
    "plan_sha256": "6e5ca96db22c71921d45c455d75c755c428369a204f5af73d117c1711dc71480",
    "generation_sha256": "10a2e17386567b2638567768944e6c48bde806a84c570782e68f653309ab0f46",
    "analysis_sha256": "c3f458c5a3bb8ba44411e7fae6e9edb98868f4d9df27234a88f9a7777ffc52af",
}
PROSPECTIVE_V2_PRIOR_NON_EVALUABLE_ATTEMPT = {
    "protocol_id": PROSPECTIVE_PROTOCOL_ID,
    "plan_sha256": "60003e4ea397456faf981bc5a954396760257e66b851ef942a819f132cb00f28",
    "status": "non_evaluable_incomplete_attempt",
    "accepted_generation_records": 0,
}
CLOSURE_TIMEOUT_SECONDS = 120.0
CLOSURE_MODEL_STRATUM = "official-deepseek-v4"
CLOSURE_REQUEST_MODEL = "deepseek-v4-flash"
CLOSURE_EXPECTED_RESPONSE_MODEL = "deepseek-v4-flash"
CLOSURE_PROVIDER_PROFILE = "deepseek-official-openai-compatible"
CLOSURE_CANARY_SHA256 = (
    "940f07c4e78e2ebca8581e73f35c57eb91fbb4352312fcc5fe4afde2b61a9228"
)
CLOSURE_ROUTE_BINDING_SHA256 = (
    "02bd12bf58025f50146c47ac7ecb891fd68e37efe0d4dda8692b5947961e5964"
)
CLOSURE_ACCEPTED_RESPONSE_CONTRACT = {
    "provider_models": ["deepseek-v4-flash"],
    "finish_reasons": ["stop", "length"],
    "max_output_tokens": 256,
    "seed_supported": False,
    "require_zero_reasoning_tokens": True,
    "prompt_cache_mode": "complete",
    "provider_fingerprint_mode": "exact_sha256",
    "provider_fingerprint_sha256": (
        "91f8ef44b1c8653bc20d23dd35056dc56984f7afef3f29860fdc389b394a8778"
    ),
}
PROSPECTIVE_V2_CANARY_SHA256 = (
    "ce63aeb61ad73335d02459a71a5e432906a539b8db226b3fd0f77b44f249bd6d"
)
PROSPECTIVE_V2_ROUTE_BINDING_SHA256 = (
    "0f9971ca63a7ff619b163bb31baf763da652eab5642d8d3d9208646fb20c03fa"
)
PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT = {
    **CLOSURE_ACCEPTED_RESPONSE_CONTRACT,
    "provider_fingerprint_sha256": (
        "c4414aeeb35e200f6ba45110ee8d3ef7e846d1228830d9bf3306ed1ddb3f3859"
    ),
}
CLOSURE_CANARY_PATH = (
    Path(__file__).resolve().parents[1]
    / "artifacts"
    / "v3-canaries-20260812-r2"
    / "deepseek-official.json"
)


class ClosureError(ValueError):
    """A closure plan, generation artifact, or action is malformed."""


@dataclass(frozen=True)
class ClosureProtocolSpec:
    """Frozen identifiers that separate development from replication targets."""

    protocol_id: str
    world_seeds: tuple[int, ...]
    target_seed_namespace: str
    motif_selection_namespace: str
    evidence_scope: str
    canary_artifact_sha256: str
    route_binding_sha256: str
    accepted_response_contract: Mapping[str, Any]
    neutral_calls_per_world: int
    calls_per_world: int
    expected_call_count: int
    expected_factual_call_count: int
    classification_mode: Literal[
        "development_event",
        "prospective_event_replication",
        "layered_world_endpoints",
    ]

    @property
    def factual_calls_per_world(self) -> int:
        return self.calls_per_world - self.neutral_calls_per_world


_PROTOCOLS = {
    CLOSURE_PROTOCOL_ID: ClosureProtocolSpec(
        protocol_id=CLOSURE_PROTOCOL_ID,
        world_seeds=CLOSURE_WORLD_SEEDS,
        target_seed_namespace=CLOSURE_TARGET_SEED_NAMESPACE,
        motif_selection_namespace=CLOSURE_MOTIF_SELECTION_NAMESPACE,
        evidence_scope=CLOSURE_EVIDENCE_SCOPE,
        canary_artifact_sha256=CLOSURE_CANARY_SHA256,
        route_binding_sha256=CLOSURE_ROUTE_BINDING_SHA256,
        accepted_response_contract=CLOSURE_ACCEPTED_RESPONSE_CONTRACT,
        neutral_calls_per_world=1,
        calls_per_world=CLOSURE_CALLS_PER_WORLD,
        expected_call_count=CLOSURE_EXPECTED_CALLS,
        expected_factual_call_count=CLOSURE_EXPECTED_FACTUAL_CALLS,
        classification_mode="development_event",
    ),
    PROSPECTIVE_PROTOCOL_ID: ClosureProtocolSpec(
        protocol_id=PROSPECTIVE_PROTOCOL_ID,
        world_seeds=PROSPECTIVE_WORLD_SEEDS,
        target_seed_namespace=PROSPECTIVE_TARGET_SEED_NAMESPACE,
        motif_selection_namespace=PROSPECTIVE_MOTIF_SELECTION_NAMESPACE,
        evidence_scope=PROSPECTIVE_EVIDENCE_SCOPE,
        canary_artifact_sha256=CLOSURE_CANARY_SHA256,
        route_binding_sha256=CLOSURE_ROUTE_BINDING_SHA256,
        accepted_response_contract=CLOSURE_ACCEPTED_RESPONSE_CONTRACT,
        neutral_calls_per_world=1,
        calls_per_world=CLOSURE_CALLS_PER_WORLD,
        expected_call_count=CLOSURE_EXPECTED_CALLS,
        expected_factual_call_count=CLOSURE_EXPECTED_FACTUAL_CALLS,
        classification_mode="prospective_event_replication",
    ),
    PROSPECTIVE_V2_PROTOCOL_ID: ClosureProtocolSpec(
        protocol_id=PROSPECTIVE_V2_PROTOCOL_ID,
        world_seeds=PROSPECTIVE_V2_WORLD_SEEDS,
        target_seed_namespace=PROSPECTIVE_V2_TARGET_SEED_NAMESPACE,
        motif_selection_namespace=PROSPECTIVE_V2_MOTIF_SELECTION_NAMESPACE,
        evidence_scope=PROSPECTIVE_V2_EVIDENCE_SCOPE,
        canary_artifact_sha256=PROSPECTIVE_V2_CANARY_SHA256,
        route_binding_sha256=PROSPECTIVE_V2_ROUTE_BINDING_SHA256,
        accepted_response_contract=PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT,
        neutral_calls_per_world=1,
        calls_per_world=CLOSURE_CALLS_PER_WORLD,
        expected_call_count=CLOSURE_EXPECTED_CALLS,
        expected_factual_call_count=CLOSURE_EXPECTED_FACTUAL_CALLS,
        classification_mode="prospective_event_replication",
    ),
    LAYERED_PROTOCOL_ID: ClosureProtocolSpec(
        protocol_id=LAYERED_PROTOCOL_ID,
        world_seeds=LAYERED_WORLD_SEEDS,
        target_seed_namespace=LAYERED_TARGET_SEED_NAMESPACE,
        motif_selection_namespace=LAYERED_MOTIF_SELECTION_NAMESPACE,
        evidence_scope=LAYERED_EVIDENCE_SCOPE,
        canary_artifact_sha256=LAYERED_CANARY_SHA256,
        route_binding_sha256=PROSPECTIVE_V2_ROUTE_BINDING_SHA256,
        accepted_response_contract=PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT,
        neutral_calls_per_world=0,
        calls_per_world=LAYERED_CALLS_PER_WORLD,
        expected_call_count=LAYERED_EXPECTED_CALLS,
        expected_factual_call_count=LAYERED_EXPECTED_FACTUAL_CALLS,
        classification_mode="layered_world_endpoints",
    ),
}


class ClosureGenerator(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        temperature: float,
        max_output_tokens: int,
        round_index: int,
        candidate_index: int,
    ) -> Any:
        ...


@dataclass(frozen=True)
class ParsedAction:
    """The small action language emitted by the model."""

    operation: Literal["no_op", "replace", "wrap_binary"]
    path: tuple[int, int] | None = None
    binary_operator: Literal["add", "sub", "mul"] | None = None
    motif_side: Literal["left", "right"] | None = None

    def __post_init__(self) -> None:
        if self.operation == "no_op":
            if (
                self.path is not None
                or self.binary_operator is not None
                or self.motif_side is not None
            ):
                raise ClosureError("no_op cannot carry edit fields")
            return
        if self.path not in EDIT_PATHS:
            raise ClosureError("action path is outside the frozen predicate operands")
        if self.operation == "replace":
            if self.binary_operator is not None or self.motif_side is not None:
                raise ClosureError("replace cannot carry wrap fields")
            return
        if self.operation != "wrap_binary":
            raise ClosureError("unknown closure action operation")
        if self.binary_operator not in WRAP_OPERATORS:
            raise ClosureError("wrap_binary operator is outside the frozen grammar")
        if self.motif_side not in ("left", "right"):
            raise ClosureError("wrap_binary side is outside the frozen grammar")
        if self.binary_operator in {"add", "mul"} and self.motif_side != "right":
            raise ClosureError(
                "commutative wrap actions use the frozen canonical right side"
            )

    @property
    def is_no_op(self) -> bool:
        return self.operation == "no_op"

    def to_dict(self) -> dict[str, Any]:
        if self.is_no_op:
            return {"operation": "no_op"}
        payload: dict[str, Any] = {
            "operation": self.operation,
            "path": list(self.path or ()),
        }
        if self.operation == "wrap_binary":
            payload.update(
                binary_operator=self.binary_operator,
                motif_side=self.motif_side,
            )
        return payload


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _derive_target_seed(world_seed: int, namespace: str) -> int:
    if type(world_seed) is not int:
        raise TypeError("world_seed must be an integer")
    if not isinstance(namespace, str) or not namespace:
        raise TypeError("namespace must be a non-empty string")
    payload = f"{namespace}:target:{world_seed}".encode("ascii")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


def derive_closure_target_seed(world_seed: int) -> int:
    """Derive a development-v1 hidden-target RNG seed (legacy public API)."""

    return _derive_target_seed(world_seed, CLOSURE_TARGET_SEED_NAMESPACE)


def _target_seed_for_namespace(world_seed: int, namespace: str) -> int:
    # Preserve the original development helper as an observable seam used by
    # existing target/motif-independence tests and small research notebooks.
    if namespace == CLOSURE_TARGET_SEED_NAMESPACE:
        return derive_closure_target_seed(world_seed)
    return _derive_target_seed(world_seed, namespace)


def _target_seed_digest(
    world_seed: int, *, namespace: str = CLOSURE_TARGET_SEED_NAMESPACE
) -> str:
    payload = f"{namespace}:target:{world_seed}".encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _stratum_for(
    world_index: int,
    factual_index: int,
    *,
    factual_calls_per_world: int = CLOSURE_FACTUAL_CALLS_PER_WORLD,
) -> str:
    # Flattening the 6 x 3 factual slots and cycling four strata yields the
    # most balanced possible 18-slot allocation: 5, 5, 4, 4.
    flat_index = world_index * factual_calls_per_world + factual_index
    return MOTIF_STRATA[flat_index % len(MOTIF_STRATA)]


def _select_motif(
    world_seed: int,
    slot_index: int,
    stratum: str,
    *,
    namespace: str = CLOSURE_MOTIF_SELECTION_NAMESPACE,
):
    candidates = tuple(
        sorted(
            (motif for motif in build_motif_library() if motif.stratum == stratum),
            key=lambda motif: (motif.canonical_hash, motif.motif_id),
        )
    )
    if not candidates:
        raise ClosureError(f"empty motif stratum: {stratum}")
    selection_key = f"{namespace}:{world_seed}:{slot_index}:{stratum}"
    index = int.from_bytes(
        hashlib.sha256(selection_key.encode("ascii")).digest(), "big"
    ) % len(candidates)
    return (
        candidates[index],
        hashlib.sha256(selection_key.encode("ascii")).hexdigest(),
    )


def _public_world_entry(
    world_index: int,
    world_seed: int,
    *,
    target_seed_namespace: str,
    include_target_seed: bool,
) -> dict[str, Any]:
    # target_seed=0 is only a convenient constructor for the target-blind bank,
    # split and D0.  Every member of the conditioned bank has the same D0 labels.
    world = generate_spark_world(world_seed, target_seed=0)
    parent = select_parent(world)
    paths = []
    for path in EDIT_PATHS:
        subtree = get_subtree(parent, path)
        paths.append(
            {
                "path": list(path),
                "expected_old_subtree_hash": dsl.canonical_hash(subtree),
                "old_subtree": dsl.to_sexpr(subtree),
            }
        )
    entry: dict[str, Any] = {
        "world_index": world_index,
        "world_seed": world_seed,
        "D0": [
            {"point": list(example.point), "label": example.label}
            for example in world.train
        ],
        "parent": dsl.to_sexpr(parent),
        "parent_canonical_hash": dsl.canonical_hash(parent),
        "allowed_paths": paths,
    }
    if include_target_seed:
        entry.update(
            target_seed=_target_seed_for_namespace(
                world_seed, target_seed_namespace
            ),
            target_seed_namespace_sha256=_target_seed_digest(
                world_seed, namespace=target_seed_namespace
            ),
        )
    return entry


def _protocol_spec(protocol_id: str) -> ClosureProtocolSpec:
    try:
        return _PROTOCOLS[protocol_id]
    except (KeyError, TypeError) as exc:
        raise ClosureError(f"unknown closure protocol: {protocol_id!r}") from exc


def _is_prospective_protocol(protocol: ClosureProtocolSpec | str) -> bool:
    """Return whether this is one of the legacy strict-event replications."""

    spec = protocol if isinstance(protocol, ClosureProtocolSpec) else _protocol_spec(protocol)
    return spec.classification_mode == "prospective_event_replication"


def _requires_hidden_target_barrier(
    protocol: ClosureProtocolSpec | str,
) -> bool:
    """Return whether target materialization must wait for sealed live generation."""

    spec = protocol if isinstance(protocol, ClosureProtocolSpec) else _protocol_spec(protocol)
    return spec.classification_mode != "development_event"


def _uses_layered_world_endpoints(
    protocol: ClosureProtocolSpec | str,
) -> bool:
    """Return whether the protocol's primary unit and classification are worlds."""

    spec = protocol if isinstance(protocol, ClosureProtocolSpec) else _protocol_spec(protocol)
    return spec.classification_mode == "layered_world_endpoints"


def _legacy_is_prospective_protocol(protocol: ClosureProtocolSpec | str) -> bool:
    """Deprecated implementation seam retained during the protocol split."""

    protocol_id = (
        protocol.protocol_id
        if isinstance(protocol, ClosureProtocolSpec)
        else protocol
    )
    return protocol_id in {PROSPECTIVE_PROTOCOL_ID, PROSPECTIVE_V2_PROTOCOL_ID}


def _model_route_for_protocol(
    protocol: ClosureProtocolSpec,
) -> dict[str, Any]:
    return {
        "model_stratum": CLOSURE_MODEL_STRATUM,
        "provider": CLOSURE_PROVIDER_PROFILE,
        "request_model": CLOSURE_REQUEST_MODEL,
        "response_model": CLOSURE_EXPECTED_RESPONSE_MODEL,
        "canary_artifact_sha256": protocol.canary_artifact_sha256,
        "route_binding_sha256": protocol.route_binding_sha256,
    }


def build_closure_plan(
    *,
    world_seeds: Sequence[int] | None = None,
    protocol_id: str = CLOSURE_PROTOCOL_ID,
) -> dict[str, Any]:
    """Build the target-independent, JSON-ready generation plan."""

    protocol_spec = _protocol_spec(protocol_id)
    seeds = protocol_spec.world_seeds if world_seeds is None else tuple(world_seeds)
    if not seeds or any(type(seed) is not int for seed in seeds):
        raise ClosureError("world_seeds must be a non-empty integer sequence")
    if len(set(seeds)) != len(seeds):
        raise ClosureError("world_seeds must be unique")
    if (
        _requires_hidden_target_barrier(protocol_spec)
        and seeds != protocol_spec.world_seeds
    ):
        raise ClosureError(
            f"{protocol_spec.protocol_id} uses exactly its frozen world seeds"
        )

    worlds = [
        _public_world_entry(
            world_index,
            seed,
            target_seed_namespace=protocol_spec.target_seed_namespace,
            include_target_seed=not _requires_hidden_target_barrier(protocol_spec),
        )
        for world_index, seed in enumerate(seeds)
    ]
    slots: list[dict[str, Any]] = []
    serial_index = 0
    stratum_counts = {stratum: 0 for stratum in MOTIF_STRATA}
    for world_index, world_seed in enumerate(seeds):
        for neutral_index in range(protocol_spec.neutral_calls_per_world):
            slots.append(
                {
                    "serial_index": serial_index,
                    "slot_id": (
                        f"world-{world_seed}:neutral"
                        if protocol_spec.neutral_calls_per_world == 1
                        else f"world-{world_seed}:neutral-{neutral_index + 1}"
                    ),
                    "world_index": world_index,
                    "world_seed": world_seed,
                    "slot_index": neutral_index,
                    "condition": "neutral",
                    "motif_id": None,
                    "motif_stratum": None,
                    "motif": None,
                    "motif_selection_sha256": None,
                }
            )
            serial_index += 1
        for factual_index in range(protocol_spec.factual_calls_per_world):
            slot_index = factual_index + 1
            stratum = _stratum_for(
                world_index,
                factual_index,
                factual_calls_per_world=protocol_spec.factual_calls_per_world,
            )
            motif, selection_digest = _select_motif(
                world_seed,
                slot_index,
                stratum,
                namespace=protocol_spec.motif_selection_namespace,
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
            serial_index += 1

    plan_without_digest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "spark-closure-generation-plan",
        "protocol_id": protocol_spec.protocol_id,
        "target_seed_namespace": protocol_spec.target_seed_namespace,
        "motif_selection_namespace": protocol_spec.motif_selection_namespace,
        "evidence_scope": protocol_spec.evidence_scope,
        "source_manifest_sha256": source_manifest(PROJECT_ROOT)[
            "source_manifest_sha256"
        ],
        "model_route": _model_route_for_protocol(protocol_spec),
        "world_seeds": list(seeds),
        "worlds": worlds,
        "slots": slots,
        "stratum_counts": stratum_counts,
        "protocol": {
            "generation_then_analysis_barrier": True,
            "calls_per_world": protocol_spec.calls_per_world,
            "temperature": CLOSURE_TEMPERATURE,
            "max_output_tokens": CLOSURE_MAX_OUTPUT_TOKENS,
            "thinking": "disabled",
            "physical_attempts_per_slot": 1,
            "generation_reads_target_or_evidence_or_test_outcomes": False,
            "analysis_max_oracle_queries": CLOSURE_MAX_ROUNDS,
            "first_query": "generated_child_or_parent_control",
            "remaining_query_rule": "shortest_bank_member_then_canonical_hash",
        },
    }
    if _uses_layered_world_endpoints(protocol_spec):
        plan_without_digest["protocol"].update(
            neutral_calls_per_world=protocol_spec.neutral_calls_per_world,
            factual_calls_per_world=protocol_spec.factual_calls_per_world,
            expected_call_count=protocol_spec.expected_call_count,
            expected_factual_call_count=protocol_spec.expected_factual_call_count,
            primary_analysis_unit="world",
            classification_mode=protocol_spec.classification_mode,
        )
    if _is_prospective_protocol(protocol_spec):
        plan_without_digest["replication_of"] = dict(PROSPECTIVE_REPLICATION_OF)
    if protocol_id == PROSPECTIVE_V2_PROTOCOL_ID:
        plan_without_digest["prior_non_evaluable_attempt"] = dict(
            PROSPECTIVE_V2_PRIOR_NON_EVALUABLE_ATTEMPT
        )
    if protocol_id == LAYERED_PROTOCOL_ID:
        plan_without_digest["follows_prospective_v2"] = dict(
            LAYERED_PRIOR_PROSPECTIVE_V2
        )
    return {
        **plan_without_digest,
        "plan_sha256": _sha256_json(plan_without_digest),
    }


def _world_for_slot(plan: Mapping[str, Any], slot: Mapping[str, Any]) -> Mapping[str, Any]:
    worlds = plan.get("worlds")
    if not isinstance(worlds, list):
        raise ClosureError("plan.worlds must be a list")
    matches = [
        world
        for world in worlds
        if isinstance(world, Mapping) and world.get("world_seed") == slot.get("world_seed")
    ]
    if len(matches) != 1:
        raise ClosureError("slot does not identify exactly one plan world")
    return matches[0]


def build_closure_prompt(
    plan: Mapping[str, Any], slot: Mapping[str, Any]
) -> str:
    """Render one public-only prompt; no oracle/private data are included."""

    world = _world_for_slot(plan, slot)
    d0 = world["D0"]
    rendered_d0 = "\n".join(
        f"  {tuple(row['point'])} -> {row['label']}" for row in d0
    )
    common = (
        "You are choosing one replayable edit action for a finite symbolic "
        "binary classifier experiment. Return exactly one JSON object with "
        "the single key expression. Do not return prose, markdown, a child "
        "program, or any additional key.\n\n"
        f"Public observations D0:\n{rendered_d0}\n\n"
        f"Frozen parent:\n{world['parent']}\n\n"
    )
    if slot.get("condition") == "neutral":
        return (
            common
            + "Assigned spark: NULL / NO_MOTIF\n"
            + "The only permitted response is:\n"
            + '{"expression":"(no_op)"}\n'
        )

    path_lines = "\n".join(
        "  path "
        + " ".join(str(value) for value in item["path"])
        + f" | expected_old_subtree_hash={item['expected_old_subtree_hash']}"
        + f" | old_subtree={item['old_subtree']}"
        for item in world["allowed_paths"]
    )
    return (
        common
        + f"Assigned spark motif ({slot['motif_stratum']}):\n{slot['motif']}\n"
        + f"Assigned motif id: {slot['motif_id']}\n\n"
        + "Allowed predicate operand paths and their frozen old-subtree hashes:\n"
        + path_lines
        + "\n\nChoose exactly one action. The complete action grammar is:\n"
        + "  (edit replace 1 1)\n"
        + "  (edit replace 1 2)\n"
        + "  (edit wrap_binary 1 1 OP SIDE)\n"
        + "  (edit wrap_binary 1 2 OP SIDE)\n"
        + "where OP is add, sub, or mul. For add or mul, SIDE must be right; "
        + "for sub, SIDE may be left or right. replace substitutes the assigned "
        + "motif for the old subtree. wrap_binary with SIDE=right substitutes "
        + "(OP old_subtree motif), while SIDE=left substitutes "
        + "(sub motif old_subtree). The chosen "
        + "path implicitly binds the displayed expected_old_subtree_hash. Try "
        + "to keep the child binary and consistent with every D0 row, and use "
        + "the assigned motif exactly once.\n\n"
        + "Example output shape (choose your own legal action):\n"
        + '{"expression":"(edit replace 1 1)"}\n'
    )


# Short descriptive alias used by small research notebooks.
build_prompt = build_closure_prompt


def parse_action(expression: Any) -> ParsedAction:
    """Parse exactly one expression in the frozen closure action grammar."""

    if not isinstance(expression, str):
        raise ClosureError("action expression must be a string")
    text = expression.strip()
    if text == "(no_op)":
        return ParsedAction("no_op")
    if not (text.startswith("(") and text.endswith(")")):
        raise ClosureError("action must be one parenthesized form")
    tokens = text[1:-1].split()
    if len(tokens) == 5 and tokens[:2] == ["edit", "replace"]:
        # This branch is intentionally unreachable: replace has four tokens.
        raise ClosureError("replace action has extra fields")
    if len(tokens) == 4 and tokens[:2] == ["edit", "replace"]:
        try:
            path = (int(tokens[2]), int(tokens[3]))
        except ValueError as exc:
            raise ClosureError("replace path must contain integers") from exc
        return ParsedAction("replace", path=path)
    if len(tokens) == 6 and tokens[:2] == ["edit", "wrap_binary"]:
        try:
            path = (int(tokens[2]), int(tokens[3]))
        except ValueError as exc:
            raise ClosureError("wrap_binary path must contain integers") from exc
        return ParsedAction(
            "wrap_binary",
            path=path,
            binary_operator=tokens[4],  # type: ignore[arg-type]
            motif_side=tokens[5],  # type: ignore[arg-type]
        )
    raise ClosureError("expression does not match the frozen action grammar")


def _response_expression(response: Any) -> Any:
    if isinstance(response, GenerationResponse):
        return response.expression
    if isinstance(response, Mapping):
        return response.get("expression")
    return getattr(response, "expression", response if isinstance(response, str) else None)


def _telemetry(response: Any) -> dict[str, Any]:
    names = (
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "provider_request_count",
        "seed_supported",
        "provider_model",
        "finish_reason",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
        "reasoning_tokens",
        "candidate_format",
    )
    if isinstance(response, Mapping):
        return {name: response.get(name) for name in names}
    return {name: getattr(response, name, None) for name in names}


def _validate_live_response(
    response: Any, *, response_contract: AcceptedResponseContract
) -> GenerationResponse:
    """Validate the frozen one-shot route without judging action content."""

    if not isinstance(response, GenerationResponse):
        raise ClosureError("live closure generator must return GenerationResponse")
    try:
        response_contract.validate(response)
    except (TypeError, ValueError, V3ResponseContractError) as exc:
        raise ClosureError("provider response violates the frozen canary contract") from exc
    return response


def _partial_generation_artifact(
    plan: Mapping[str, Any], records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    protocol_spec = _plan_protocol_spec(plan)
    partial_without_digest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "spark-closure-generation-partial",
        "protocol_id": _plan_protocol_spec(plan).protocol_id,
        "evidence_scope": plan["evidence_scope"],
        "plan_sha256": plan["plan_sha256"],
        "generation_complete_before_target_analysis": False,
        "resume_supported": False,
        "call_count": len(records),
        "expected_call_count": len(plan["slots"]),
        "records": [dict(record) for record in records],
    }
    if _requires_hidden_target_barrier(protocol_spec):
        partial_without_digest["live_response_contract_validated"] = False
    return {
        **partial_without_digest,
        "partial_sha256": _sha256_json(partial_without_digest),
    }


def _plan_protocol_spec(plan: Mapping[str, Any]) -> ClosureProtocolSpec:
    """Resolve new protocol metadata while accepting sealed legacy dev plans."""

    protocol_id = plan.get("protocol_id")
    legacy_development_plan = protocol_id is None
    if legacy_development_plan:
        spec = _PROTOCOLS[CLOSURE_PROTOCOL_ID]
        if plan.get("evidence_scope") != spec.evidence_scope:
            raise ClosureError("legacy closure plan has an unknown evidence scope")
        for field, expected in (
            ("target_seed_namespace", spec.target_seed_namespace),
            ("motif_selection_namespace", spec.motif_selection_namespace),
        ):
            if field in plan and plan.get(field) != expected:
                raise ClosureError(f"legacy closure plan has an invalid {field}")
        return spec

    if not isinstance(protocol_id, str):
        raise ClosureError("closure plan protocol id must be a string")
    spec = _protocol_spec(protocol_id)
    expected_metadata = {
        "target_seed_namespace": spec.target_seed_namespace,
        "motif_selection_namespace": spec.motif_selection_namespace,
        "evidence_scope": spec.evidence_scope,
    }
    for field, expected in expected_metadata.items():
        if plan.get(field) != expected:
            raise ClosureError(f"closure plan {field} differs from its protocol")
    return spec


def _validate_plan_envelope(plan: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(plan, Mapping):
        raise ClosureError("closure plan must be an object")
    if plan.get("schema_version") != 1 or plan.get("kind") != "spark-closure-generation-plan":
        raise ClosureError("unsupported closure plan schema")
    protocol_spec = _plan_protocol_spec(plan)
    source_digest = plan.get("source_manifest_sha256")
    if (
        not isinstance(source_digest, str)
        or len(source_digest) != 64
        or any(character not in "0123456789abcdef" for character in source_digest)
    ):
        raise ClosureError("closure plan source manifest digest is malformed")
    expected_route = _model_route_for_protocol(protocol_spec)
    if plan.get("model_route") != expected_route:
        raise ClosureError("closure plan model route differs from the amendment")
    digest = plan.get("plan_sha256")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if not isinstance(digest, str) or digest != _sha256_json(unsigned):
        raise ClosureError("closure plan digest mismatch")
    slots = plan.get("slots")
    worlds = plan.get("worlds")
    seeds = plan.get("world_seeds")
    if not isinstance(slots, list) or not isinstance(worlds, list) or not isinstance(seeds, list):
        raise ClosureError("closure plan worlds, seeds, and slots must be lists")
    if (
        _requires_hidden_target_barrier(protocol_spec)
        and tuple(seeds) != protocol_spec.world_seeds
    ):
        raise ClosureError(
            f"{protocol_spec.protocol_id} plan differs from its frozen world seeds"
        )
    if (
        len(worlds) != len(seeds)
        or len(slots) != protocol_spec.calls_per_world * len(seeds)
    ):
        raise ClosureError("closure plan has an inconsistent world/call count")
    if [world.get("world_seed") for world in worlds if isinstance(world, Mapping)] != seeds:
        raise ClosureError("closure plan world ordering is inconsistent")
    if _requires_hidden_target_barrier(protocol_spec) and any(
        not isinstance(world, Mapping)
        or "target_seed" in world
        or "target_seed_namespace_sha256" in world
        for world in worlds
    ):
        raise ClosureError("prospective plan must not materialize hidden target seeds")
    if _is_prospective_protocol(protocol_spec) and plan.get(
        "replication_of"
    ) != PROSPECTIVE_REPLICATION_OF:
        raise ClosureError("prospective plan does not bind the frozen development result")
    if (
        protocol_spec.protocol_id == PROSPECTIVE_V2_PROTOCOL_ID
        and plan.get("prior_non_evaluable_attempt")
        != PROSPECTIVE_V2_PRIOR_NON_EVALUABLE_ATTEMPT
    ):
        raise ClosureError("prospective-v2 does not bind the retired v1 attempt")
    if (
        protocol_spec.protocol_id == LAYERED_PROTOCOL_ID
        and plan.get("follows_prospective_v2") != LAYERED_PRIOR_PROSPECTIVE_V2
    ):
        raise ClosureError("layered-v1 does not bind the completed prospective-v2 result")
    if _uses_layered_world_endpoints(protocol_spec):
        protocol_value = plan.get("protocol")
        layered_protocol_fields = {
            "calls_per_world": protocol_spec.calls_per_world,
            "neutral_calls_per_world": protocol_spec.neutral_calls_per_world,
            "factual_calls_per_world": protocol_spec.factual_calls_per_world,
            "expected_call_count": protocol_spec.expected_call_count,
            "expected_factual_call_count": protocol_spec.expected_factual_call_count,
            "primary_analysis_unit": "world",
            "classification_mode": protocol_spec.classification_mode,
        }
        if not isinstance(protocol_value, Mapping) or any(
            protocol_value.get(field) != expected
            for field, expected in layered_protocol_fields.items()
        ):
            raise ClosureError("layered-v1 plan protocol counts are malformed")
        expected_stratum_count = protocol_spec.expected_factual_call_count // len(
            MOTIF_STRATA
        )
        if plan.get("stratum_counts") != {
            stratum: expected_stratum_count for stratum in MOTIF_STRATA
        }:
            raise ClosureError("layered-v1 motif strata are not balanced")
    normalized: list[Mapping[str, Any]] = []
    expected_layout = (
        ([0] * protocol_spec.neutral_calls_per_world)
        + list(range(1, protocol_spec.factual_calls_per_world + 1))
    )
    for serial_index, slot in enumerate(slots):
        if not isinstance(slot, Mapping) or slot.get("serial_index") != serial_index:
            raise ClosureError("closure slot order is malformed")
        if slot.get("condition") not in ("neutral", "motif"):
            raise ClosureError("closure slot condition is malformed")
        if slot.get("slot_index") not in expected_layout:
            raise ClosureError("closure slot index is malformed")
        normalized.append(slot)
    for world_index, world_seed in enumerate(seeds):
        world_slots = [
            slot for slot in normalized if slot.get("world_seed") == world_seed
        ]
        if (
            [slot.get("world_index") for slot in world_slots]
            != [world_index] * protocol_spec.calls_per_world
            or [slot.get("slot_index") for slot in world_slots] != expected_layout
            or [slot.get("condition") for slot in world_slots]
            != (["neutral"] * protocol_spec.neutral_calls_per_world)
            + (["motif"] * protocol_spec.factual_calls_per_world)
        ):
            raise ClosureError("closure per-world slot layout is malformed")
    return tuple(normalized)


def _require_current_source_manifest(plan: Mapping[str, Any]) -> None:
    """Refuse a paid/live attempt when code differs from the sealed plan."""

    if plan.get("source_manifest_sha256") != source_manifest(PROJECT_ROOT).get(
        "source_manifest_sha256"
    ):
        raise ClosureError("closure implementation source manifest drifted")


def generate_closure(
    plan: Mapping[str, Any],
    generator: ClosureGenerator,
    *,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    response_contract: AcceptedResponseContract | None = None,
) -> dict[str, Any]:
    """Perform exactly one generation call per planned slot.

    The returned records deliberately omit prompts, assistant text, raw
    responses, endpoint information, credentials, and reconstructed children.
    Malformed action text consumes its slot and is represented only by a closed
    parse status.
    """

    slots = _validate_plan_envelope(plan)
    protocol_spec = _plan_protocol_spec(plan)
    if not callable(getattr(generator, "generate", None)):
        raise TypeError("generator must expose generate()")
    prospective_live_contract_validated = False
    if response_contract is not None:
        if response_contract.to_dict() != dict(
            protocol_spec.accepted_response_contract
        ):
            raise ClosureError(
                f"{protocol_spec.protocol_id} response contract differs from "
                "its frozen canary"
            )
        prospective_live_contract_validated = _requires_hidden_target_barrier(
            protocol_spec
        )
    records: list[dict[str, Any]] = []
    for slot in slots:
        prompt = build_closure_prompt(plan, slot)
        response = generator.generate(
            prompt,
            temperature=CLOSURE_TEMPERATURE,
            max_output_tokens=CLOSURE_MAX_OUTPUT_TOKENS,
            round_index=int(slot["world_index"]),
            candidate_index=int(slot["slot_index"]),
        )
        if response_contract is not None:
            response = _validate_live_response(
                response, response_contract=response_contract
            )
        try:
            parsed = parse_action(_response_expression(response))
        except ClosureError:
            parsed = None
        records.append(
            {
                "serial_index": slot["serial_index"],
                "slot_id": slot["slot_id"],
                "world_index": slot["world_index"],
                "world_seed": slot["world_seed"],
                "slot_index": slot["slot_index"],
                "condition": slot["condition"],
                "motif_id": slot["motif_id"],
                "action_parse_valid": parsed is not None,
                "action": None if parsed is None else parsed.to_dict(),
                "parse_failure": None if parsed is not None else "invalid_action_grammar",
                "telemetry": _telemetry(response),
            }
        )
        if progress_callback is not None:
            progress_callback(_partial_generation_artifact(plan, records))

    artifact_without_digest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "spark-closure-generation",
        "protocol_id": _plan_protocol_spec(plan).protocol_id,
        "evidence_scope": plan["evidence_scope"],
        "plan_sha256": plan["plan_sha256"],
        "generation_complete_before_target_analysis": True,
        "call_count": len(records),
        "records": records,
    }
    if _requires_hidden_target_barrier(protocol_spec):
        artifact_without_digest["live_response_contract_validated"] = (
            prospective_live_contract_validated
        )
        artifact_without_digest["canary_artifact_sha256"] = (
            protocol_spec.canary_artifact_sha256
        )
        artifact_without_digest["route_binding_sha256"] = (
            protocol_spec.route_binding_sha256
        )
    return {
        **artifact_without_digest,
        "generation_sha256": _sha256_json(artifact_without_digest),
    }


def _parsed_action_fields(value: Any) -> ParsedAction | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ClosureError("generation action fields must be an object or null")
    operation = value.get("operation")
    if operation == "no_op":
        if set(value) != {"operation"}:
            raise ClosureError("saved no_op contains unexpected fields")
        return ParsedAction("no_op")
    if operation not in ("replace", "wrap_binary"):
        raise ClosureError("saved edit operation is malformed")
    path_value = value.get("path")
    if not isinstance(path_value, list) or len(path_value) != 2:
        raise ClosureError("saved action path is malformed")
    path = tuple(path_value)
    if any(type(item) is not int for item in path):
        raise ClosureError("saved action path is malformed")
    if operation == "replace":
        if set(value) != {"operation", "path"}:
            raise ClosureError("saved replace contains unexpected fields")
        return ParsedAction("replace", path=path)  # type: ignore[arg-type]
    if set(value) != {"operation", "path", "binary_operator", "motif_side"}:
        raise ClosureError("saved wrap_binary contains unexpected fields")
    return ParsedAction(
        "wrap_binary",
        path=path,  # type: ignore[arg-type]
        binary_operator=value.get("binary_operator"),  # type: ignore[arg-type]
        motif_side=value.get("motif_side"),  # type: ignore[arg-type]
    )


def _validate_generation_envelope(
    plan: Mapping[str, Any], generation: Mapping[str, Any]
) -> tuple[tuple[Mapping[str, Any], ParsedAction | None], ...]:
    """Validate every sealed generation record without constructing a target."""

    slots = _validate_plan_envelope(plan)
    if not isinstance(generation, Mapping):
        raise ClosureError("generation artifact must be an object")
    if generation.get("schema_version") != 1 or generation.get("kind") != "spark-closure-generation":
        raise ClosureError("unsupported generation artifact schema")
    if generation.get("plan_sha256") != plan.get("plan_sha256"):
        raise ClosureError("generation artifact belongs to another plan")
    if generation.get("evidence_scope") != plan.get("evidence_scope"):
        raise ClosureError("generation evidence scope differs from its plan")
    protocol_spec = _plan_protocol_spec(plan)
    generation_protocol_id = generation.get("protocol_id")
    if generation_protocol_id is not None and generation_protocol_id != (
        protocol_spec.protocol_id
    ):
        raise ClosureError("generation protocol differs from its plan")
    if _requires_hidden_target_barrier(protocol_spec):
        if generation_protocol_id != protocol_spec.protocol_id:
            raise ClosureError("prospective generation protocol metadata is missing")
        if generation.get("live_response_contract_validated") is not True:
            raise ClosureError(
                "prospective generation lacks a validated live response contract"
            )
        if (
            generation.get("canary_artifact_sha256")
            != protocol_spec.canary_artifact_sha256
            or generation.get("route_binding_sha256")
            != protocol_spec.route_binding_sha256
        ):
            raise ClosureError("prospective generation route binding is missing")
    unsigned = {
        key: value for key, value in generation.items() if key != "generation_sha256"
    }
    if generation.get("generation_sha256") != _sha256_json(unsigned):
        raise ClosureError("generation artifact digest mismatch")
    records = generation.get("records")
    if (
        generation.get("generation_complete_before_target_analysis") is not True
        or not isinstance(records, list)
        or generation.get("call_count") != len(slots)
        or len(records) != len(slots)
    ):
        raise ClosureError("generation artifact is incomplete")

    result: list[tuple[Mapping[str, Any], ParsedAction | None]] = []
    identity_fields = (
        "serial_index",
        "slot_id",
        "world_index",
        "world_seed",
        "slot_index",
        "condition",
        "motif_id",
    )
    for slot, record in zip(slots, records, strict=True):
        if not isinstance(record, Mapping):
            raise ClosureError("generation record must be an object")
        if any(record.get(name) != slot.get(name) for name in identity_fields):
            raise ClosureError("generation record identity differs from its slot")
        parse_valid = record.get("action_parse_valid")
        if type(parse_valid) is not bool:
            raise ClosureError("generation parse validity must be boolean")
        parsed = _parsed_action_fields(record.get("action"))
        if parse_valid != (parsed is not None):
            raise ClosureError("generation parse status disagrees with action fields")
        if parse_valid and record.get("parse_failure") is not None:
            raise ClosureError("valid generation record carries a parse failure")
        if not parse_valid and record.get("parse_failure") != "invalid_action_grammar":
            raise ClosureError("invalid generation record has unknown parse status")
        if not isinstance(record.get("telemetry"), Mapping):
            raise ClosureError("generation telemetry must be an object")
        result.append((record, parsed))
    return tuple(result)


def _path_hash(world_entry: Mapping[str, Any], path: tuple[int, int]) -> str:
    for item in world_entry["allowed_paths"]:
        if tuple(item["path"]) == path:
            return str(item["expected_old_subtree_hash"])
    raise ClosureError("saved action path has no frozen subtree hash")


def _trajectory_summary(result: CompressionResult) -> dict[str, Any]:
    return {
        "seed_canonical_hash": result.seed_canonical_hash,
        "N_t": list(result.N_t),
        "N_T": result.N_T,
        "rounds_completed": result.rounds_completed,
        "truth_retained": result.truth_retained,
        "full_domain_recovered": result.full_domain_recovered,
        "exact_identification": result.exact_identification,
        "termination_reason": result.termination_reason,
        "certified_fact_count": result.certified_fact_count,
        "contraction_bits": result.cumulative_log_ratio.bits,
        "non_match_response_count": sum(
            not step.response.is_match for step in result.steps
        ),
        "positive_non_match_contraction": any(
            not step.response.is_match and step.N_after < step.N_before
            for step in result.steps
        ),
    }


def _direct_hit(compressor: SparkCompressor, ast: dsl.Expr) -> bool:
    return dsl.behavior_vector(ast, compressor.domain) == compressor.target_behavior


def _match_lineage(
    records: Sequence[LineageRecord],
    parsed: ParsedAction,
    motif_id: str,
    expected_old_subtree_hash: str,
) -> LineageRecord | None:
    if parsed.is_no_op or parsed.path is None:
        return None
    action = EditAction(
        operation=parsed.operation,  # type: ignore[arg-type]
        path=parsed.path,
        expected_old_subtree_hash=expected_old_subtree_hash,
        motif_id=motif_id,
        binary_operator=parsed.binary_operator,
        motif_side=parsed.motif_side,
    )
    matches = [record for record in records if record.action == action]
    if len(matches) > 1:
        raise ClosureError("generated action ambiguously matches multiple lineages")
    return matches[0] if matches else None


def _analyze_factual_slot(
    *,
    slot: Mapping[str, Any],
    parsed: ParsedAction | None,
    plan_world: Mapping[str, Any],
    world: SparkWorld,
    compressor: SparkCompressor,
    parent: dsl.Expr,
    parent_result: CompressionResult,
    lineages: Sequence[LineageRecord],
) -> dict[str, Any]:
    parent_direct = _direct_hit(compressor, parent)
    base: dict[str, Any] = {
        "slot_id": slot["slot_id"],
        "serial_index": slot["serial_index"],
        "slot_index": slot["slot_index"],
        "condition": "motif",
        "motif_id": slot["motif_id"],
        "motif_stratum": slot["motif_stratum"],
        "action_parse_valid": parsed is not None,
        "lineage_valid": False,
        "lineage_failure": None,
        "factorial_outcomes": {
            "Y00": parent_direct,
            "Y10": None,
            "Y01": parent_result.exact_identification,
            "Y11": None,
        },
        "factorial_definition": {
            "first_index": "assigned_spark_action_absent_0_present_1",
            "second_index": "oracle_evidence_absent_0_present_1",
            "success": "complete_domain_target_recovered_and_unique_when_evidence_present",
        },
        "parent_trajectory": _trajectory_summary(parent_result),
        "strict_event": False,
    }
    if parsed is None:
        base["lineage_failure"] = "action_parse_invalid"
        return base
    if parsed.is_no_op:
        base["lineage_failure"] = "no_op_in_factual_slot"
        return base

    motif_id = str(slot["motif_id"])
    # Lookup also proves that the saved assignment still names the frozen motif.
    motif_by_id(motif_id)
    assert parsed.path is not None
    expected_hash = _path_hash(plan_world, parsed.path)
    try:
        lineage = _match_lineage(lineages, parsed, motif_id, expected_hash)
    except (TypeError, ValueError):
        lineage = None
    if lineage is None:
        base["lineage_failure"] = "not_in_frozen_reachable_lineage_set"
        return base

    child = lineage.child_ast
    child_direct = _direct_hit(compressor, child)
    child_result = compressor.run(child, max_rounds=CLOSURE_MAX_ROUNDS)
    replacements = lineage.matched_replacements[:2]
    if len(replacements) != 2:
        # enumerate_reachable_children normally makes this impossible; retain a
        # closed failure in case the upstream lineage contract changes.
        base["lineage_failure"] = "fewer_than_two_matched_replacements"
        return base
    replacement_rows: list[dict[str, Any]] = []
    replacement_successes: list[bool] = []
    for replacement in replacements:
        replacement_result = compressor.run(
            replacement.child_ast, max_rounds=CLOSURE_MAX_ROUNDS
        )
        replacement_successes.append(replacement_result.exact_identification)
        replacement_rows.append(
            {
                "motif_id": replacement.motif_id,
                "motif_stratum": replacement.motif_stratum,
                "child_canonical_hash": replacement.child_canonical_hash,
                "direct_hit": _direct_hit(compressor, replacement.child_ast),
                "trajectory": _trajectory_summary(replacement_result),
            }
        )

    y11 = child_result.exact_identification
    positive_mediated_step = any(
        not step.response.is_match and step.N_after < step.N_before
        for step in child_result.steps
    )
    strict_checks = {
        "lineage_valid": True,
        "child_not_direct_hit": not child_direct,
        "child_truth_retained": child_result.truth_retained,
        "child_terminal_singleton": child_result.N_T == 1,
        "child_full_domain_recovered": child_result.full_domain_recovered,
        "parent_deletion_does_not_reach_endpoint": not parent_result.exact_identification,
        "at_least_one_matched_replacement_does_not_reach_endpoint": not all(
            replacement_successes
        ),
        "positive_non_match_oracle_contraction": positive_mediated_step,
    }
    base.update(
        {
            "lineage_valid": True,
            "lineage_failure": None,
            "lineage_hash": lineage.lineage_hash,
            "action_hash": lineage.action_hash,
            "child_canonical_hash": lineage.child_canonical_hash,
            "child_behavior_hash": lineage.child_behavior_hash,
            "child_direct_hit": child_direct,
            "factorial_outcomes": {
                "Y00": parent_direct,
                "Y10": child_direct,
                "Y01": parent_result.exact_identification,
                "Y11": y11,
            },
            "child_trajectory": _trajectory_summary(child_result),
            "matched_replacements": replacement_rows,
            "strict_event_checks": strict_checks,
            "strict_event": all(strict_checks.values()),
        }
    )
    return base


def classify_closure_outcome(
    *, protocol_id: str, strict_event_count: int, valid_lineage_count: int
) -> str:
    """Classify aggregate counts without reading worlds, targets, or artifacts."""

    _protocol_spec(protocol_id)
    if (
        type(strict_event_count) is not int
        or type(valid_lineage_count) is not int
        or strict_event_count < 0
        or valid_lineage_count < 0
        or strict_event_count > valid_lineage_count
    ):
        raise ClosureError("closure outcome counts are inconsistent")

    if _is_prospective_protocol(protocol_id):
        if strict_event_count:
            return "prospective_mechanism_instance_replicated"
        if valid_lineage_count:
            return "prospective_replication_not_observed"
        return "prospective_lineage_interface_failure"

    if strict_event_count:
        return "exploratory_mechanism_instance_observed"
    if valid_lineage_count:
        return "lineage_feasible_but_no_strict_event"
    return "model_lineage_interface_not_feasible"


def classify_layered_outcome(specificity_world_count: int) -> str:
    """Classify the number of independent worlds reaching the strong endpoint."""

    if type(specificity_world_count) is not int or specificity_world_count < 0:
        raise ClosureError("layered specificity world count is invalid")
    if specificity_world_count >= 2:
        return "prospective_cross_world_replication_observed"
    if specificity_world_count == 1:
        return "single_prospective_mechanism_instance_observed"
    return "not_observed_under_frozen_protocol"


def _layered_slot_endpoints(row: Mapping[str, Any]) -> dict[str, bool]:
    """Evaluate nested slot gates without selecting among generated actions."""

    lineage = row.get("lineage_valid") is True
    child = row.get("child_trajectory")
    parent = row.get("parent_trajectory")
    replacements = row.get("matched_replacements")
    child_mapping = child if isinstance(child, Mapping) else {}
    parent_mapping = parent if isinstance(parent, Mapping) else {}
    replacement_rows = replacements if isinstance(replacements, list) else []
    replacement_closures = [
        replacement.get("trajectory", {}).get("exact_identification") is True
        for replacement in replacement_rows
        if isinstance(replacement, Mapping)
        and isinstance(replacement.get("trajectory"), Mapping)
    ]
    mediated_closure = bool(
        lineage
        and row.get("child_direct_hit") is False
        and child_mapping.get("truth_retained") is True
        and child_mapping.get("N_T") == 1
        and child_mapping.get("full_domain_recovered") is True
        and child_mapping.get("positive_non_match_contraction") is True
        and isinstance(child_mapping.get("rounds_completed"), int)
        and child_mapping.get("rounds_completed") <= CLOSURE_MAX_ROUNDS
    )
    parent_deletion = bool(
        mediated_closure
        and parent_mapping.get("exact_identification") is False
    )
    two_frozen_replacements = len(replacement_closures) == 2
    weak_replacement = bool(
        parent_deletion
        and two_frozen_replacements
        and not all(replacement_closures)
    )
    strong_replacement = bool(
        parent_deletion
        and two_frozen_replacements
        and not any(replacement_closures)
    )
    return {
        "L": lineage,
        "M": mediated_closure,
        "D": parent_deletion,
        "R": strong_replacement,
        "S": strong_replacement,
        "weak_at_least_one_replacement_failure": weak_replacement,
    }


def _signed_summary(values: Sequence[float | int]) -> dict[str, Any]:
    count = len(values)
    total = sum(values)
    return {
        "count": count,
        "positive_count": sum(value > 0 for value in values),
        "tie_count": sum(value == 0 for value in values),
        "negative_count": sum(value < 0 for value in values),
        "sum": total,
        "mean": (total / count) if count else None,
    }


def _binomial_cdf(k: int, n: int, probability: float) -> float:
    return sum(
        math.comb(n, index)
        * probability**index
        * (1.0 - probability) ** (n - index)
        for index in range(k + 1)
    )


def _bisect_monotone(
    function: Callable[[float], float],
    target: float,
    *,
    increasing: bool,
) -> float:
    lower, upper = 0.0, 1.0
    for _ in range(80):
        midpoint = (lower + upper) / 2.0
        value = function(midpoint)
        if (value < target) == increasing:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def _clopper_pearson_interval(
    successes: int,
    trials: int,
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    if trials <= 0 or successes < 0 or successes > trials:
        raise ClosureError("binomial count is invalid")
    lower = (
        0.0
        if successes == 0
        else _bisect_monotone(
            lambda probability: 1.0
            - _binomial_cdf(successes - 1, trials, probability),
            alpha / 2.0,
            increasing=True,
        )
    )
    upper = (
        1.0
        if successes == trials
        else _bisect_monotone(
            lambda probability: _binomial_cdf(
                successes, trials, probability
            ),
            alpha / 2.0,
            increasing=False,
        )
    )
    return {
        "method": "equal_tailed_clopper_pearson",
        "confidence_level": 0.95,
        "lower": lower,
        "upper": upper,
    }


def summarize_layered_endpoints(
    worlds: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate layered endpoints with worlds, never slots, as the primary unit."""

    endpoint_names = ("L", "M", "D", "R", "S")
    world_rows: list[dict[str, Any]] = []
    parent_differences: list[int] = []
    replacement_1_differences: list[int] = []
    replacement_2_differences: list[int] = []
    parent_log_ratios: list[float] = []
    replacement_1_log_ratios: list[float] = []
    replacement_2_log_ratios: list[float] = []
    motif_packages: dict[str, dict[str, Any]] = {}
    slot_endpoint_counts = {
        **{name: 0 for name in endpoint_names},
        "weak_at_least_one_replacement_failure": 0,
    }
    valid_lineage_slots = 0
    for world in worlds:
        slot_results = world.get("slot_results")
        if not isinstance(slot_results, list):
            raise ClosureError("layered world has malformed slot results")
        qualifying = {
            **{name: [] for name in endpoint_names},
            "weak_at_least_one_replacement_failure": [],
        }
        for row in slot_results:
            if not isinstance(row, Mapping) or row.get("condition") != "motif":
                continue
            flags = _layered_slot_endpoints(row)
            for name, passed in flags.items():
                if passed:
                    qualifying[name].append(row.get("slot_id"))
                    slot_endpoint_counts[name] += 1
            if not flags["L"]:
                continue
            valid_lineage_slots += 1
            child = row["child_trajectory"]
            parent = row["parent_trajectory"]
            replacements = row["matched_replacements"]
            child_n = int(child["N_T"])
            if child_n <= 0:
                raise ClosureError("layered child terminal N_T must be positive")
            parent_n = int(parent["N_T"])
            parent_differences.append(parent_n - child_n)
            parent_log_ratios.append(math.log2(parent_n / child_n))
            if len(replacements) == 2:
                replacement_1_n = int(replacements[0]["trajectory"]["N_T"])
                replacement_2_n = int(replacements[1]["trajectory"]["N_T"])
                replacement_1_differences.append(replacement_1_n - child_n)
                replacement_2_differences.append(replacement_2_n - child_n)
                replacement_1_log_ratios.append(
                    math.log2(replacement_1_n / child_n)
                )
                replacement_2_log_ratios.append(
                    math.log2(replacement_2_n / child_n)
                )
        endpoints = {name: bool(qualifying[name]) for name in endpoint_names}
        if not (
            (not endpoints["M"] or endpoints["L"])
            and (not endpoints["D"] or endpoints["M"])
            and (not endpoints["R"] or endpoints["D"])
            and endpoints["R"] == endpoints["S"]
        ):
            raise ClosureError("layered world endpoints are not nested")
        package_members = tuple(
            str(row.get("motif_stratum"))
            for row in slot_results
            if isinstance(row, Mapping) and row.get("condition") == "motif"
        )
        package_id = "|".join(package_members) if package_members else "unspecified"
        package = motif_packages.setdefault(
            package_id,
            {
                "world_count": 0,
                "world_counts": {name: 0 for name in endpoint_names},
            },
        )
        package["world_count"] += 1
        for name in endpoint_names:
            package["world_counts"][name] += int(endpoints[name])
        world_rows.append(
            {
                "world_seed": world.get("world_seed"),
                "motif_package_id": package_id,
                "endpoints": endpoints,
                "endpoint_aliases": {
                    "E1": endpoints["L"],
                    "E2": endpoints["M"],
                    "E3": endpoints["D"],
                    "E4": endpoints["R"],
                },
                "weak_at_least_one_replacement_failure": bool(
                    qualifying["weak_at_least_one_replacement_failure"]
                ),
                "qualifying_slot_ids": qualifying,
            }
        )

    world_denominator = len(world_rows)
    world_counts = {
        name: sum(row["endpoints"][name] for row in world_rows)
        for name in endpoint_names
    }
    world_counts_K = {
        "K1": world_counts["L"],
        "K2": world_counts["M"],
        "K3": world_counts["D"],
        "K4": world_counts["R"],
    }
    if world_counts_K["K1"] == 0:
        bottleneck = "lineage_interface_failure"
    elif world_counts_K["K2"] == 0:
        bottleneck = "lineage_feasible_without_oracle_closure"
    elif world_counts_K["K3"] == 0:
        bottleneck = "oracle_closure_without_parent_advantage"
    elif world_counts_K["K4"] == 0:
        bottleneck = "parent_advantage_without_matched_motif_specificity"
    else:
        bottleneck = "strong_matched_motif_specificity_observed"
    rates = {
        name: {
            "numerator": count,
            "denominator": world_denominator,
            "rate": count / world_denominator if world_denominator else None,
            "clopper_pearson_95": (
                _clopper_pearson_interval(count, world_denominator)
                if world_denominator
                else None
            ),
        }
        for name, count in world_counts.items()
    }
    conditional_pairs = (("M_given_L", "M", "L"), ("D_given_M", "D", "M"), ("R_given_D", "R", "D"))
    conditional_conversions = {}
    for label, numerator_name, denominator_name in conditional_pairs:
        numerator = world_counts[numerator_name]
        denominator = world_counts[denominator_name]
        conditional_conversions[label] = {
            "numerator": numerator,
            "denominator": denominator,
            "rate": numerator / denominator if denominator else None,
            "estimability": "descriptive" if denominator else "not_estimable",
        }
    weak_world_count = sum(
        row["weak_at_least_one_replacement_failure"] for row in world_rows
    )
    return {
        "primary_unit": "world",
        "world_denominator": world_denominator,
        "definitions": {
            "L": "world_has_at_least_one_lineage_valid_factual_slot",
            "M": (
                "same_world_has_a_lineage_valid_non_direct_truth_retained_"
                "positive_nonmatch_four_round_singleton_full_recovery_slot"
            ),
            "D": "same_M_qualifying_slot_and_parent_does_not_close",
            "R": (
                "same_D_qualifying_slot_and_both_frozen_replacements_do_not_close"
            ),
            "S": "alias_of_strong_R_replacement_specificity_endpoint",
        },
        "world_counts": world_counts,
        "world_counts_K": world_counts_K,
        "world_rates": rates,
        "interval_interpretation": (
            "common-rate binomial model-based intervals; the four frozen motif "
            "packages are balanced but not identical, so pooled intervals are not "
            "unconditional design-exact confidence intervals"
        ),
        "motif_package_counts_descriptive_only": motif_packages,
        "deepest_layer_bottleneck": bottleneck,
        "conditional_conversions_descriptive_only": conditional_conversions,
        "weak_replacement_world_count": weak_world_count,
        "weak_replacement_world_rate": {
            "numerator": weak_world_count,
            "denominator": world_denominator,
            "rate": weak_world_count / world_denominator if world_denominator else None,
        },
        "slot_counts_descriptive_only": slot_endpoint_counts,
        "terminal_N_T_differences_descriptive_only": {
            "unit": "valid_lineage_slot_non_iid",
            "valid_lineage_slot_count": valid_lineage_slots,
            "positive_means_control_terminal_N_T_exceeds_child": True,
            "parent_minus_child": _signed_summary(parent_differences),
            "replacement_1_minus_child": _signed_summary(
                replacement_1_differences
            ),
            "replacement_2_minus_child": _signed_summary(
                replacement_2_differences
            ),
            "log2_parent_over_child": _signed_summary(parent_log_ratios),
            "log2_replacement_1_over_child": _signed_summary(
                replacement_1_log_ratios
            ),
            "log2_replacement_2_over_child": _signed_summary(
                replacement_2_log_ratios
            ),
        },
        "worlds": world_rows,
        "classification": classify_layered_outcome(world_counts["S"]),
    }


def analyze_closure(
    plan: Mapping[str, Any], generation: Mapping[str, Any]
) -> dict[str, Any]:
    """Analyze a completely sealed generation artifact offline.

    The first two calls validate the complete plan and all generation records.
    Only after that barrier succeeds are hidden-target worlds and compressors
    constructed.
    """

    slots = _validate_plan_envelope(plan)
    protocol_spec = _plan_protocol_spec(plan)
    validated_generation = _validate_generation_envelope(plan, generation)
    if _requires_hidden_target_barrier(protocol_spec):
        # The first target-dependent pass must execute the exact source tree
        # frozen before generation.  Legacy development artifacts remain
        # replayable under later compatible analyzers.
        _require_current_source_manifest(plan)
    parsed_by_serial = {
        int(record["serial_index"]): parsed
        for record, parsed in validated_generation
    }

    results_by_world: list[dict[str, Any]] = []
    factual_rows: list[dict[str, Any]] = []
    neutral_valid_count = 0
    for plan_world in plan["worlds"]:
        world_seed = int(plan_world["world_seed"])
        target_seed = _target_seed_for_namespace(
            world_seed, protocol_spec.target_seed_namespace
        )
        if _requires_hidden_target_barrier(protocol_spec):
            if (
                "target_seed" in plan_world
                or "target_seed_namespace_sha256" in plan_world
            ):
                raise ClosureError("prospective plan materialized a hidden target seed")
        elif plan_world.get("target_seed") != target_seed:
            raise ClosureError("plan target seed differs from frozen SHA namespace")
        world = generate_spark_world(world_seed, target_seed=target_seed)
        parent = select_parent(world)
        if (
            dsl.canonical_hash(parent) != plan_world.get("parent_canonical_hash")
            or dsl.to_sexpr(parent) != plan_world.get("parent")
        ):
            raise ClosureError("plan parent differs from the frozen world")
        actual_d0 = [
            {"point": list(example.point), "label": example.label}
            for example in world.train
        ]
        if actual_d0 != plan_world.get("D0"):
            raise ClosureError("plan D0 differs from the frozen world")

        compressor = SparkCompressor(world)
        parent_result = compressor.run(parent, max_rounds=CLOSURE_MAX_ROUNDS)
        lineages = enumerate_reachable_children(world)
        world_slots = [
            slot for slot in slots if slot.get("world_seed") == world_seed
        ]
        slot_rows: list[dict[str, Any]] = []
        for slot in world_slots:
            parsed = parsed_by_serial[int(slot["serial_index"])]
            if slot["condition"] == "neutral":
                neutral_valid = parsed is not None and parsed.is_no_op
                neutral_valid_count += neutral_valid
                slot_rows.append(
                    {
                        "slot_id": slot["slot_id"],
                        "serial_index": slot["serial_index"],
                        "slot_index": 0,
                        "condition": "neutral",
                        "action_parse_valid": parsed is not None,
                        "neutral_no_op_valid": neutral_valid,
                        "Y00": _direct_hit(compressor, parent),
                        "Y01": parent_result.exact_identification,
                        "parent_trajectory": _trajectory_summary(parent_result),
                        "strict_event": False,
                    }
                )
                continue
            row = _analyze_factual_slot(
                slot=slot,
                parsed=parsed,
                plan_world=plan_world,
                world=world,
                compressor=compressor,
                parent=parent,
                parent_result=parent_result,
                lineages=lineages,
            )
            slot_rows.append(row)
            factual_rows.append(row)
        results_by_world.append(
            {
                "world_seed": world_seed,
                "target_seed_namespace_sha256": _target_seed_digest(
                    world_seed, namespace=protocol_spec.target_seed_namespace
                ),
                "world_hash": world.world_hash,
                "target_index": world.target_index,
                "target_canonical_hash": dsl.canonical_hash(world.target),
                "parent_canonical_hash": dsl.canonical_hash(parent),
                "slot_results": slot_rows,
                "strict_event_count": sum(
                    bool(row.get("strict_event")) for row in slot_rows
                ),
            }
        )

    valid_lineages = sum(bool(row["lineage_valid"]) for row in factual_rows)
    strict_events = sum(bool(row["strict_event"]) for row in factual_rows)
    layered_summary = (
        summarize_layered_endpoints(results_by_world)
        if _uses_layered_world_endpoints(protocol_spec)
        else None
    )
    if layered_summary is not None:
        by_seed = {
            row["world_seed"]: row for row in layered_summary["worlds"]
        }
        for world_row in results_by_world:
            endpoint_row = by_seed[world_row["world_seed"]]
            world_row["layered_endpoints"] = endpoint_row["endpoints"]
            world_row["layered_qualifying_slot_ids"] = endpoint_row[
                "qualifying_slot_ids"
            ]
            world_row["weak_at_least_one_replacement_failure"] = endpoint_row[
                "weak_at_least_one_replacement_failure"
            ]
        mechanism_classification = str(layered_summary["classification"])
    else:
        mechanism_classification = classify_closure_outcome(
            protocol_id=protocol_spec.protocol_id,
            strict_event_count=strict_events,
            valid_lineage_count=valid_lineages,
        )

    # Only the exact protocol schedule may emit its frozen classification.
    # Smaller scopes remain useful diagnostics but are not protocol results.
    canonical_scope = (
        list(plan.get("world_seeds", ())) == list(protocol_spec.world_seeds)
        and len(slots) == protocol_spec.expected_call_count
        and sum(slot.get("condition") == "neutral" for slot in slots)
        == protocol_spec.neutral_calls_per_world * len(protocol_spec.world_seeds)
        and sum(slot.get("condition") == "motif" for slot in slots)
        == protocol_spec.expected_factual_call_count
    )
    classification = (
        mechanism_classification
        if canonical_scope
        else "diagnostic_only_noncanonical_scope"
    )

    y_counts = {
        name: sum(row["factorial_outcomes"].get(name) is True for row in factual_rows)
        for name in ("Y00", "Y10", "Y01", "Y11")
    }
    report_without_digest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "spark-closure-offline-analysis",
        "evidence_scope": protocol_spec.evidence_scope,
        "confirmatory_evidence": False,
        "strict_reachable_gate_remains_failed": True,
        "plan_sha256": plan["plan_sha256"],
        "generation_sha256": generation["generation_sha256"],
        "generation_fully_sealed_before_target_analysis": True,
        "model_call_count": len(slots),
        "factual_slot_count": len(factual_rows),
        "neutral_slot_count": len(slots) - len(factual_rows),
        "neutral_no_op_valid_count": neutral_valid_count,
        "valid_lineage_count": valid_lineages,
        "strict_event_count": strict_events,
        "factorial_success_counts": y_counts,
        "canonical_24_call_scope": canonical_scope,
        "classification": classification,
        "diagnostic_mechanism_classification": mechanism_classification,
        "interpretation_limit": (
            "prospective replication of a mechanism instance only; not evidence for "
            "entropy causation, prevalence, or broad generalization"
            if _is_prospective_protocol(protocol_spec)
            else "existence/feasibility signal only; not a confirmatory positive or negative"
        ),
        "worlds": results_by_world,
    }
    if layered_summary is not None:
        report_without_digest.pop("canonical_24_call_scope")
        report_without_digest.pop("diagnostic_mechanism_classification")
        report_without_digest.update(
            canonical_protocol_scope=canonical_scope,
            classification=classification,
            diagnostic_layered_classification=mechanism_classification,
            layered_endpoints={
                key: value
                for key, value in layered_summary.items()
                if key != "worlds"
            },
            strict_event_count_secondary_legacy_weak_endpoint=strict_events,
            interpretation_limit=(
                "world-level layered mechanism follow-up only; slot counts and terminal "
                "N_T differences are descriptive and non-IID; not evidence for entropy "
                "causation, prevalence, or broad generalization"
            ),
        )
    # Preserve the byte-for-byte scientific object produced by the sealed
    # pre-protocol-id development artifacts.  Explicit protocol plans carry
    # the new metadata; legacy replays do not acquire retrospective fields.
    if plan.get("protocol_id") is not None and layered_summary is None:
        report_without_digest.update(
            protocol_id=protocol_spec.protocol_id,
            target_seed_namespace=protocol_spec.target_seed_namespace,
            motif_selection_namespace=protocol_spec.motif_selection_namespace,
            prospective_replication_evidence=(
                canonical_scope
                and _is_prospective_protocol(protocol_spec)
            ),
        )
    elif layered_summary is not None:
        report_without_digest.update(
            protocol_id=protocol_spec.protocol_id,
            target_seed_namespace=protocol_spec.target_seed_namespace,
            motif_selection_namespace=protocol_spec.motif_selection_namespace,
            prospective_layered_evidence=canonical_scope,
        )
    return {
        **report_without_digest,
        "analysis_sha256": _sha256_json(report_without_digest),
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClosureError(f"cannot read JSON artifact {path}") from exc
    if not isinstance(value, dict):
        raise ClosureError(f"JSON artifact {path} must contain an object")
    return value


def _emit_json(value: Mapping[str, Any], output: Path | None) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if output is None:
        print(rendered, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())


def validate_closure_canary(
    credentials: ProviderCredentials,
    canary_path: str | Path,
    *,
    protocol_id: str = CLOSURE_PROTOCOL_ID,
) -> tuple[OpenAICompatibleGenerator, AcceptedResponseContract, Mapping[str, Any]]:
    """Bind execution to the route canary frozen by one closure protocol."""

    protocol_spec = _protocol_spec(protocol_id)
    if credentials.model != CLOSURE_REQUEST_MODEL:
        raise ClosureError("closure credentials name another request model")
    try:
        binding = model_binding_from_canary(
            canary_path,
            credentials,
            expected_stratum_id=CLOSURE_MODEL_STRATUM,
        )
        contract_value = binding["accepted_response_contract"]
        contract = AcceptedResponseContract(
            provider_models=tuple(contract_value["provider_models"]),
            finish_reasons=tuple(contract_value["finish_reasons"]),
            max_output_tokens=int(contract_value["max_output_tokens"]),
            seed_supported=contract_value["seed_supported"],
            require_zero_reasoning_tokens=contract_value[
                "require_zero_reasoning_tokens"
            ],
            prompt_cache_mode=str(contract_value["prompt_cache_mode"]),
            provider_fingerprint_mode=str(
                contract_value["provider_fingerprint_mode"]
            ),
            provider_fingerprint_sha256=contract_value[
                "provider_fingerprint_sha256"
            ],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ClosureError("official DeepSeek V3 canary binding is invalid") from exc
    if contract.to_dict() != dict(protocol_spec.accepted_response_contract):
        raise ClosureError(
            f"V3 canary response contract differs from {protocol_id}"
        )
    if (
        binding.get("provider") != CLOSURE_PROVIDER_PROFILE
        or binding.get("name") != CLOSURE_REQUEST_MODEL
        or binding.get("snapshot") != CLOSURE_EXPECTED_RESPONSE_MODEL
    ):
        raise ClosureError("V3 canary binds a different model identity")
    canary_evidence = binding.get("canary_evidence")
    if not isinstance(canary_evidence, Mapping) or (
        canary_evidence.get("artifact_sha256")
        != protocol_spec.canary_artifact_sha256
        or canary_evidence.get("route_binding_sha256")
        != protocol_spec.route_binding_sha256
    ):
        raise ClosureError(
            f"V3 canary differs from the artifact frozen by {protocol_id}"
        )
    return build_v3_generator(credentials), contract, binding


def _live_generator(
    prefix: str,
    env_file: Path | None,
    canary_path: Path,
    *,
    protocol_id: str = CLOSURE_PROTOCOL_ID,
) -> tuple[OpenAICompatibleGenerator, AcceptedResponseContract, Mapping[str, Any]]:
    credentials = load_provider_credentials(prefix=prefix, env_file=env_file)
    return validate_closure_canary(
        credentials, canary_path, protocol_id=protocol_id
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="build the frozen 24-call plan")
    plan_parser.add_argument("--output", type=Path)
    plan_parser.add_argument(
        "--protocol",
        choices=tuple(_PROTOCOLS),
        default=CLOSURE_PROTOCOL_ID,
        help=(
            "development-v1 by default; choose a prospective protocol "
            "explicitly for its frozen untouched replication worlds"
        ),
    )

    generate_parser = commands.add_parser(
        "generate", help="execute and seal all planned model actions"
    )
    generate_parser.add_argument("--plan", type=Path, required=True)
    generate_parser.add_argument("--output", type=Path, required=True)
    generate_parser.add_argument("--execute", action="store_true")
    generate_parser.add_argument("--env-prefix", default="DEEPSEEK")
    generate_parser.add_argument("--env-file", type=Path)
    generate_parser.add_argument(
        "--canary",
        type=Path,
        default=CLOSURE_CANARY_PATH,
        help="previously passed official DeepSeek V3 route-canary artifact",
    )

    analyze_parser = commands.add_parser(
        "analyze", help="run target-dependent oracle analysis after generation"
    )
    analyze_parser.add_argument("--plan", type=Path, required=True)
    analyze_parser.add_argument("--generation", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path)

    args = parser.parse_args(argv)
    if args.command == "plan":
        _emit_json(build_closure_plan(protocol_id=args.protocol), args.output)
        return 0
    if args.command == "generate":
        if not args.execute:
            parser.error("generate requires --execute; no network call was made")
        plan = _read_json(args.plan)
        _validate_plan_envelope(plan)
        protocol_spec = _plan_protocol_spec(plan)
        # The plan remains structurally readable and analyzable after later
        # source changes, but a new paid attempt must use the exact source tree
        # sealed into that plan.  This check precedes provider construction and
        # therefore every possible network request.
        _require_current_source_manifest(plan)
        generator, response_contract, _binding = _live_generator(
            args.env_prefix,
            args.env_file,
            args.canary,
            protocol_id=protocol_spec.protocol_id,
        )
        result = generate_closure(
            plan,
            generator,
            response_contract=response_contract,
            # Persist a sanitized incomplete artifact after every accepted
            # response.  It is not resumable or analyzable, but a failure at a
            # later slot cannot erase the observations from earlier paid slots.
            progress_callback=lambda partial: _emit_json(partial, args.output),
        )
        _emit_json(result, args.output)
        return 0
    if args.command == "analyze":
        plan = _read_json(args.plan)
        generation = _read_json(args.generation)
        _emit_json(analyze_closure(plan, generation), args.output)
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CLOSURE_CALLS_PER_WORLD",
    "CLOSURE_ACCEPTED_RESPONSE_CONTRACT",
    "CLOSURE_EVIDENCE_SCOPE",
    "CLOSURE_EXPECTED_CALLS",
    "CLOSURE_EXPECTED_FACTUAL_CALLS",
    "CLOSURE_FACTUAL_CALLS_PER_WORLD",
    "CLOSURE_MAX_OUTPUT_TOKENS",
    "CLOSURE_MAX_ROUNDS",
    "CLOSURE_MOTIF_SELECTION_NAMESPACE",
    "CLOSURE_PROTOCOL_ID",
    "CLOSURE_SEED_NAMESPACE",
    "CLOSURE_TARGET_SEED_NAMESPACE",
    "CLOSURE_TEMPERATURE",
    "CLOSURE_WORLD_SEEDS",
    "LAYERED_CALLS_PER_WORLD",
    "LAYERED_CANARY_SHA256",
    "LAYERED_EVIDENCE_SCOPE",
    "LAYERED_EXPECTED_CALLS",
    "LAYERED_EXPECTED_FACTUAL_CALLS",
    "LAYERED_FACTUAL_CALLS_PER_WORLD",
    "LAYERED_MOTIF_SELECTION_NAMESPACE",
    "LAYERED_PRIOR_PROSPECTIVE_V2",
    "LAYERED_PROTOCOL_ID",
    "LAYERED_TARGET_SEED_NAMESPACE",
    "LAYERED_WORLD_SEEDS",
    "PROSPECTIVE_EVIDENCE_SCOPE",
    "PROSPECTIVE_MOTIF_SELECTION_NAMESPACE",
    "PROSPECTIVE_PROTOCOL_ID",
    "PROSPECTIVE_REPLICATION_OF",
    "PROSPECTIVE_TARGET_SEED_NAMESPACE",
    "PROSPECTIVE_WORLD_SEEDS",
    "PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT",
    "PROSPECTIVE_V2_CANARY_SHA256",
    "PROSPECTIVE_V2_EVIDENCE_SCOPE",
    "PROSPECTIVE_V2_MOTIF_SELECTION_NAMESPACE",
    "PROSPECTIVE_V2_PRIOR_NON_EVALUABLE_ATTEMPT",
    "PROSPECTIVE_V2_PROTOCOL_ID",
    "PROSPECTIVE_V2_ROUTE_BINDING_SHA256",
    "PROSPECTIVE_V2_TARGET_SEED_NAMESPACE",
    "PROSPECTIVE_V2_WORLD_SEEDS",
    "ClosureError",
    "ClosureProtocolSpec",
    "ParsedAction",
    "analyze_closure",
    "build_closure_plan",
    "build_closure_prompt",
    "build_prompt",
    "classify_closure_outcome",
    "classify_layered_outcome",
    "derive_closure_target_seed",
    "generate_closure",
    "main",
    "parse_action",
    "summarize_layered_endpoints",
    "validate_closure_canary",
]
