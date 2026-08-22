"""Fair-choice model benchmark built from the sealed strong-K4 scan.

This module is deliberately split at the provider information boundary.  The
public manifest contains only opaque task ids and already-rendered prompts.
The private key contains pair identities, option mappings, deterministic
endpoint outcomes, and frozen shortcut baselines.  No function in this module
issues a model or provider request.

The sham spark is selected before any target-dependent replay.  Selection sees
only a target-free public world, motif stratum/complexity, and each motif's
ten-bit K1 support mask.  K2--K4 are computed symmetrically for the factual and
sham arms only after that choice has been fixed.
"""

from __future__ import annotations

import base64
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import dsl, spark_closure, spark_lineage
from . import spark_strong_k4_scan as strong_scan
from .spark_compressor import SparkCompressor
from .spark_lineage import ArithmeticMotif, EditAction, LineageRecord
from .spark_world import SparkWorld, _world_structure
from .provenance import PROJECT_ROOT, source_manifest
from .world_generator import Example


SCHEMA_VERSION = 1
PROTOCOL_ID = "spark-strong-k4-fair-choice-v1"
PUBLIC_MANIFEST_KIND = "spark-strong-k4-fair-choice-public-manifest"
PRIVATE_KEY_KIND = "spark-strong-k4-fair-choice-private-key"
OFFLINE_SCORE_KIND = "spark-strong-k4-fair-choice-offline-score"
PAIR_COUNT = 32
TASK_COUNT = 64
RAW_ACTION_COUNT = 10
ARMS = ("factual", "sham")
ENDPOINT_NAMES = ("K1", "K2", "K3", "K4_full_pool")
MINIMUM_UNIQUE_CONTROL_BEHAVIORS = 3

SEALED_SCAN_PROTOCOL_ID = "spark-strong-k4-feasibility-v2"
SEALED_SCAN_SHA256 = (
    "e5e69e46fecbf9a6bea1a540281b4579f2b7f1902697352007e6153a2360ab91"
)
SEALED_SCAN_FILE_SHA256 = (
    "1ec8f0262fd1c27024e9c6d25702b1f1d799c8693cc7f082805ea434cf855c1a"
)
SEALED_SCAN_PLAN_FILE_SHA256 = (
    "e99ceee07a8472c8694516fc537dd04c40b00efe0a4e8d950e3dbe0390c0fb98"
)
SEALED_SCAN_PLAN_SHA256 = (
    "9f00e5811ae19cf6337988781aa4e4b094b44477e19d688745e66b51eb5b09bc"
)
SEALED_SCAN_CONFIG_FILE_SHA256 = (
    "78dd0bf573f38cf55aa1812a19ee3778e8bb2ea22783244a86c88099bd532ac0"
)
SEALED_SCAN_SOURCE_MANIFEST_SHA256 = (
    "f1af9c04ab97307e42a9bfae0a4a1618b0db1af3d677340e420b9564dc303a0c"
)
SEALED_SCAN_ARTIFACT_COMMIT = "612ff5a7cd67347bd0c1ecacaa1453358073e0f7"
SEALED_SCAN_SOURCE_FREEZE_COMMIT = "c703428bad23afc6214723de2a50025a5091cac4"
SEALED_SCAN_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "spark-strong-k4-feasibility-v2-20260822"
    / "result.json"
)
SEALED_SCAN_PLAN_PATH = SEALED_SCAN_PATH.with_name("plan.json")
SEALED_SCAN_CONFIG_PATH = PROJECT_ROOT / "configs" / "spark-strong-k4-feasibility-v2.json"

SHAM_TIE_NAMESPACE = f"{PROTOCOL_ID}:matched-sham"
ACTION_ORDER_NAMESPACE = f"{PROTOCOL_ID}:base-option-permutation"
OPAQUE_OPTION_NAMESPACE = f"{PROTOCOL_ID}:opaque-option-id"
OPAQUE_ID_NAMESPACE = f"{PROTOCOL_ID}:opaque-id"

FIXED_RAW_POLICY_IDS = tuple(
    f"fixed-semantic-{raw_index:02d}" for raw_index in range(RAW_ACTION_COUNT)
)
FIXED_DISPLAY_POLICY_IDS = tuple(
    f"fixed-display-position-{position:02d}"
    for position in range(RAW_ACTION_COUNT)
)
PUBLIC_K1_POLICY_IDS = (
    "first-public-K1-else-first-displayed",
    "public-k1-min-node-hash",
    "public-k1-min-positive-node-hash",
    "public-k1-max-parent-novelty-node-hash",
)
BASELINE_POLICY_IDS = (
    FIXED_RAW_POLICY_IDS + FIXED_DISPLAY_POLICY_IDS + PUBLIC_K1_POLICY_IDS
)

EXPECTED_SHAM_HAMMING_COUNTS = {0: 20, 1: 6, 2: 4, 3: 2}
EXPECTED_FACTUAL_K4_FRAME_COUNT = 53
EXPECTED_FACTUAL_K4_FRAMES_SHAM_K1_SUPPORTED = 50
EXPECTED_ALL_FACTUAL_K4_FRAMES_SHAM_K1_SUPPORTED_WORLD_COUNT = 29
EXPECTED_SHAM_K2_FAILURE_AMONG_K1_SUPPORTED = 34
EXPECTED_SHAM_K2_FAILURE_WITH_K1_INVALID_AS_MISS = 37
EXPECTED_ALL_FACTUAL_K4_FRAMES_SHAM_K2_FAILED_WORLD_COUNT = 19

CANONICAL_ROUTE_IDS = ("deepseek-flash", "deepseek-pro", "glm-5.2")

PRIMARY_ENDPOINT_CONTRACT = {
    "name": "paired_symmetric_usefulness_U",
    "arm_definition": "selected_action_K2",
    "K3_identical_to_K2_in_selected_parent_open_cohort": True,
    "test": "exact_one_sided_paired_sign_McNemar_factual_greater_sham",
}
STRONG_FACTUAL_ENDPOINT_CONTRACT = {
    "name": "F",
    "definition": "factual_arm_selected_action_K4_full_pool",
}

FAIR_CONFIG_PATH = PROJECT_ROOT / "configs" / "spark-strong-k4-fair-choice-v1.json"
# Byte seal for the preregistered fair-choice protocol.  Any later protocol
# edit must deliberately create a new seal before a benchmark can be built.
FAIR_CONFIG_FILE_SHA256 = (
    "b40679c6be18f6c3bb3f54a360e554af75567fa84aec64a5593513d3384173d9"
)

_PROMPT_FORBIDDEN_TERMS = (
    "candidate",
    "condition",
    "control",
    "factual",
    "hash",
    "index",
    "k1",
    "k2",
    "k3",
    "k4",
    "motif",
    "private",
    "seed",
    "sham",
    "slot",
    "stratum",
    "target",
    "true",
    "true arm",
    "witness",
    "world",
)
_LONG_INTEGER_RE = re.compile(r"(?<![A-Za-z0-9])\d{10,}(?![A-Za-z0-9])")
_SHA256_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.I)


class FairChoiceError(ValueError):
    """A sealed input, fair-choice design, or response set is malformed."""


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
        raise FairChoiceError("fair-choice values must be canonical JSON") from exc


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def fair_config_file_sha256() -> str:
    """Return the exact config identity after enforcing its byte seal."""

    try:
        observed = hashlib.sha256(FAIR_CONFIG_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        raise FairChoiceError("cannot read the fair-choice config") from exc
    if observed != FAIR_CONFIG_FILE_SHA256:
        raise FairChoiceError("fair-choice config bytes differ from the frozen seal")
    return observed


def _load_frozen_fair_config() -> Mapping[str, Any]:
    """Load only the byte-sealed preregistration used for runtime assertions."""

    fair_config_file_sha256()
    try:
        value = json.loads(FAIR_CONFIG_PATH.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FairChoiceError("cannot parse the frozen fair-choice config") from exc
    if not isinstance(value, Mapping):
        raise FairChoiceError("frozen fair-choice config must be an object")
    return value


def _opaque_id(kind: str, payload: object) -> str:
    digest = hashlib.sha256(
        f"{OPAQUE_ID_NAMESPACE}:{kind}:".encode("ascii")
        + _canonical_json_bytes(payload)
    ).digest()
    token = base64.b32encode(digest).decode("ascii").rstrip("=")[:14]
    return f"{kind.upper()}-{token}"


def _fraction_payload(value: Fraction) -> dict[str, Any]:
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "value": float(value),
    }


@dataclass(frozen=True)
class K1SupportProfile:
    """The complete target-free information allowed into sham selection."""

    motif_id: str
    motif_sexpr: str
    motif_canonical_hash: str
    motif_behavior_hash: str
    stratum: str
    complexity_bucket: tuple[int, int]
    k1_mask: tuple[bool, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.motif_id, str) or not self.motif_id:
            raise FairChoiceError("K1 profile motif id must be non-empty")
        if not isinstance(self.motif_sexpr, str) or not self.motif_sexpr:
            raise FairChoiceError("K1 profile motif expression must be non-empty")
        if not _is_sha256(self.motif_canonical_hash) or not _is_sha256(
            self.motif_behavior_hash
        ):
            raise FairChoiceError("K1 profile motif hashes are malformed")
        if self.stratum not in spark_lineage.MOTIF_STRATA:
            raise FairChoiceError("K1 profile uses an unknown motif stratum")
        if (
            not isinstance(self.complexity_bucket, tuple)
            or len(self.complexity_bucket) != 2
            or any(type(value) is not int for value in self.complexity_bucket)
        ):
            raise FairChoiceError("K1 profile complexity bucket is malformed")
        if (
            not isinstance(self.k1_mask, tuple)
            or len(self.k1_mask) != RAW_ACTION_COUNT
            or any(type(value) is not bool for value in self.k1_mask)
        ):
            raise FairChoiceError("K1 profile mask must contain ten booleans")

    def to_dict(self) -> dict[str, Any]:
        return {
            "motif_id": self.motif_id,
            "motif_sexpr": self.motif_sexpr,
            "motif_canonical_hash": self.motif_canonical_hash,
            "motif_behavior_hash": self.motif_behavior_hash,
            "stratum": self.stratum,
            "complexity_bucket": list(self.complexity_bucket),
            "k1_mask": list(self.k1_mask),
        }


def validate_sealed_scan_result(
    result: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], ...], dict[int, Mapping[str, Any]]]:
    """Validate the exact completed feasibility result used by fair-choice v1."""

    if not isinstance(result, Mapping):
        raise FairChoiceError("sealed strong-K4 result must be an object")
    unsigned = {key: value for key, value in result.items() if key != "scan_sha256"}
    if result.get("scan_sha256") != _sha256_json(unsigned):
        raise FairChoiceError("sealed strong-K4 result digest mismatch")
    if (
        result.get("schema_version") != 1
        or result.get("kind") != strong_scan.MERGED_KIND
        or result.get("protocol_id") != SEALED_SCAN_PROTOCOL_ID
        or result.get("scan_sha256") != SEALED_SCAN_SHA256
        or result.get("plan_sha256") != SEALED_SCAN_PLAN_SHA256
        or result.get("config_file_sha256") != SEALED_SCAN_CONFIG_FILE_SHA256
        or result.get("source_manifest_sha256")
        != SEALED_SCAN_SOURCE_MANIFEST_SHA256
        or result.get("model_outputs_read") is not False
        or result.get("provider_calls_made") != 0
        or result.get("outcome_conditioned_benchmark_construction") is not True
    ):
        raise FairChoiceError("input is not the exact sealed strong-K4 result")

    cohort = result.get("balanced_cohort")
    worlds = result.get("worlds")
    if not isinstance(cohort, Mapping) or not isinstance(worlds, list):
        raise FairChoiceError("sealed strong-K4 cohort or worlds are malformed")
    assignments = cohort.get("assignments")
    projection = cohort.get("future_public_projection")
    if (
        cohort.get("classification") != "full_32_balanced_feasible"
        or cohort.get("complete") is not True
        or cohort.get("required_world_count") != PAIR_COUNT
        or cohort.get("matched_world_count") != PAIR_COUNT
        or cohort.get("counts_by_construction_stratum")
        != {stratum: 8 for stratum in spark_lineage.MOTIF_STRATA}
        or not isinstance(assignments, list)
        or len(assignments) != PAIR_COUNT
        or not isinstance(projection, list)
        or len(projection) != PAIR_COUNT
        or cohort.get("future_public_projection_sha256") != _sha256_json(projection)
        or cohort.get("future_public_projection_marks_witness_slot") is not False
    ):
        raise FairChoiceError("sealed balanced cohort is not the frozen 32-world design")

    by_index: dict[int, Mapping[str, Any]] = {}
    for world in worlds:
        if not isinstance(world, Mapping) or type(world.get("candidate_index")) is not int:
            raise FairChoiceError("sealed scan contains a malformed candidate world")
        candidate_index = int(world["candidate_index"])
        if candidate_index in by_index:
            raise FairChoiceError("sealed scan duplicates a candidate world")
        by_index[candidate_index] = world
    if len(by_index) != 1024:
        raise FairChoiceError("sealed scan must contain all 1,024 candidate worlds")

    selected: set[int] = set()
    for assignment, public in zip(assignments, projection, strict=True):
        if not isinstance(assignment, Mapping) or not isinstance(public, Mapping):
            raise FairChoiceError("sealed cohort assignment is malformed")
        candidate_index = assignment.get("candidate_index")
        witnesses = assignment.get("K4_full_pool_witness_slot_ids")
        if (
            type(candidate_index) is not int
            or candidate_index in selected
            or candidate_index not in by_index
            or not isinstance(witnesses, list)
            or len(witnesses) != 1
            or not isinstance(witnesses[0], str)
            or assignment.get("all_three_slots_claimed_eligible") is not False
        ):
            raise FairChoiceError("sealed cohort world/witness binding is malformed")
        selected.add(candidate_index)
        if by_index[candidate_index].get("public_identity") != public:
            raise FairChoiceError("sealed public projection differs from its world")
    return tuple(assignments), by_index


def load_sealed_scan_result(path: str | Path = SEALED_SCAN_PATH) -> dict[str, Any]:
    """Read the exact byte-sealed feasibility result without writing artifacts."""

    source = Path(path)
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise FairChoiceError("cannot read sealed strong-K4 result") from exc
    if hashlib.sha256(payload).hexdigest() != SEALED_SCAN_FILE_SHA256:
        raise FairChoiceError("strong-K4 result file bytes differ from the seal")
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FairChoiceError("sealed strong-K4 result is invalid JSON") from exc
    if not isinstance(result, dict):
        raise FairChoiceError("sealed strong-K4 result must contain an object")
    validate_sealed_scan_result(result)
    _validate_sealed_scan_companions()
    return result


def _read_exact_json(path: Path, expected_sha256: str, label: str) -> Mapping[str, Any]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise FairChoiceError(f"cannot read sealed strong-K4 {label}") from exc
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise FairChoiceError(f"sealed strong-K4 {label} bytes differ from the seal")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise FairChoiceError(f"sealed strong-K4 {label} is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise FairChoiceError(f"sealed strong-K4 {label} must contain an object")
    return value


def _validate_sealed_scan_companions() -> None:
    """Verify the historical plan/config seals without comparing current source."""

    plan = _read_exact_json(
        SEALED_SCAN_PLAN_PATH, SEALED_SCAN_PLAN_FILE_SHA256, "plan"
    )
    plan_unsigned = {
        key: value for key, value in plan.items() if key != "plan_sha256"
    }
    if (
        plan.get("schema_version") != 1
        or plan.get("kind") != strong_scan.PLAN_KIND
        or plan.get("protocol_id") != SEALED_SCAN_PROTOCOL_ID
        or plan.get("plan_sha256") != SEALED_SCAN_PLAN_SHA256
        or plan.get("plan_sha256") != _sha256_json(plan_unsigned)
        or plan.get("config_file_sha256") != SEALED_SCAN_CONFIG_FILE_SHA256
        or plan.get("source_manifest_sha256")
        != SEALED_SCAN_SOURCE_MANIFEST_SHA256
        or plan.get("model_outputs_read") is not False
        or plan.get("provider_calls_made") != 0
        or plan.get("outcome_conditioned_benchmark_construction") is not False
    ):
        raise FairChoiceError("sealed strong-K4 plan bindings are malformed")
    config = _read_exact_json(
        SEALED_SCAN_CONFIG_PATH, SEALED_SCAN_CONFIG_FILE_SHA256, "config"
    )
    if (
        config.get("schema_version") != 1
        or config.get("kind") != "spark-strong-k4-feasibility-scan-config"
        or config.get("protocol_id") != SEALED_SCAN_PROTOCOL_ID
    ):
        raise FairChoiceError("sealed strong-K4 config identity is malformed")


def _semantic_action(raw_action_index: int) -> spark_closure.ParsedAction:
    if type(raw_action_index) is not int or not 0 <= raw_action_index < RAW_ACTION_COUNT:
        raise FairChoiceError("raw action index is outside 0..9")
    path = spark_lineage.EDIT_PATHS[raw_action_index // 5]
    frame = raw_action_index % 5
    if frame == 0:
        return spark_closure.ParsedAction("replace", path=path)
    operator, side = (
        ("add", "right"),
        ("sub", "right"),
        ("sub", "left"),
        ("mul", "right"),
    )[frame - 1]
    return spark_closure.ParsedAction(
        "wrap_binary",
        path=path,
        binary_operator=operator,  # type: ignore[arg-type]
        motif_side=side,  # type: ignore[arg-type]
    )


def _parsed_edit_action(action: EditAction) -> spark_closure.ParsedAction:
    return spark_closure.ParsedAction(
        action.operation,
        path=action.path,  # type: ignore[arg-type]
        binary_operator=action.binary_operator,
        motif_side=action.motif_side,
    )


def _action_description(raw_action_index: int) -> str:
    action = _semantic_action(raw_action_index)
    location = "PREDICATE_LEFT" if action.path == (1, 1) else "PREDICATE_RIGHT"
    if action.operation == "replace":
        edited = "CONTEXT"
    else:
        assert action.binary_operator is not None and action.motif_side is not None
        operator = action.binary_operator
        if action.motif_side == "left":
            edited = f"({operator} CONTEXT OLD)"
        else:
            edited = f"({operator} OLD CONTEXT)"
    return f"location={location}; new_subtree={edited}"


def action_order_for_pair(pair_ordinal: int) -> tuple[int, ...]:
    """Return a deterministic cyclic Latin order for one of the 32 pairs."""

    if type(pair_ordinal) is not int or not 0 <= pair_ordinal < PAIR_COUNT:
        raise FairChoiceError("pair ordinal is outside the frozen 32-pair design")
    base = tuple(
        sorted(
            range(RAW_ACTION_COUNT),
            key=lambda raw: hashlib.sha256(
                f"{ACTION_ORDER_NAMESPACE}:{raw}".encode("ascii")
            ).digest(),
        )
    )
    offset = pair_ordinal % RAW_ACTION_COUNT
    return base[offset:] + base[:offset]


def pair_anchor_sha256(
    upstream_public_identity_sha256: str, witness_slot_id: str
) -> str:
    """Bind one pair using only frozen target-free construction identities."""

    if not _is_sha256(upstream_public_identity_sha256):
        raise FairChoiceError("upstream public identity digest is malformed")
    if not isinstance(witness_slot_id, str) or not witness_slot_id:
        raise FairChoiceError("witness slot id must be non-empty")
    return _sha256_json(
        {
            "upstream_public_identity_sha256": upstream_public_identity_sha256,
            "witness_slot_id": witness_slot_id,
        }
    )


def option_ids_for_pair(pair_anchor: str) -> tuple[str, ...]:
    """Derive ten equal-length opaque IDs, one for each display position."""

    if not _is_sha256(pair_anchor):
        raise FairChoiceError("pair anchor digest is malformed")
    result = tuple(
        "Q"
        + _sha256_json(
            {
                "namespace": OPAQUE_OPTION_NAMESPACE,
                "pair_anchor_sha256": pair_anchor,
                "display_position": position,
            }
        )[:8].upper()
        for position in range(RAW_ACTION_COUNT)
    )
    if len(set(result)) != RAW_ACTION_COUNT:
        raise FairChoiceError("within-pair opaque option ID collision")
    return result


def _target_free_support_world(world_seed: int) -> SparkWorld:
    """Construct the world structure needed by K1 without drawing a target.

    Only the shared D0 signature and point locations are meaningful here.
    Evidence/test labels are inert placeholders: lineage K1 checks use their
    point sets, never their labels.  No target RNG or target index is read.
    """

    if type(world_seed) is not int:
        raise FairChoiceError("world seed must be an integer")
    hypotheses, train_points, evidence_points, test_points, group_size = (
        _world_structure(world_seed)
    )
    if not hypotheses:
        raise FairChoiceError("target-free support world has an empty bank")
    shared = hypotheses[0]
    train = tuple(Example(point, dsl.evaluate(shared, point)) for point in train_points)
    evidence = tuple(Example(point, 0) for point in evidence_points)
    test = tuple(Example(point, 0) for point in test_points)
    return SparkWorld(
        world_seed=world_seed,
        target_seed=0,
        hypotheses=hypotheses,
        target_index=0,
        train=train,
        evidence=evidence,
        test=test,
        world_hash="target-free-k1-support-only",
        reservoir_size=0,
        conditioning_group_size=group_size,
    )


def _public_context_from_world_entry(world: Mapping[str, Any]) -> dict[str, Any]:
    d0 = world.get("D0")
    parent = world.get("parent")
    paths = world.get("allowed_paths")
    if (
        not isinstance(d0, list)
        or len(d0) != 12
        or not isinstance(parent, str)
        or not isinstance(paths, list)
        or len(paths) != 2
    ):
        raise FairChoiceError("public D0/parent/path context is malformed")
    old_by_path: dict[tuple[int, int], str] = {}
    for item in paths:
        if not isinstance(item, Mapping):
            raise FairChoiceError("public editable path is malformed")
        path = item.get("path")
        old = item.get("old_subtree")
        if (
            not isinstance(path, list)
            or len(path) != 2
            or any(type(value) is not int for value in path)
            or not isinstance(old, str)
        ):
            raise FairChoiceError("public editable path is malformed")
        old_by_path[tuple(path)] = old
    if set(old_by_path) != set(spark_lineage.EDIT_PATHS):
        raise FairChoiceError("public context does not contain the two frozen paths")
    normalized_d0: list[dict[str, Any]] = []
    for row in d0:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("point"), list)
            or len(row["point"]) != 3
            or any(type(value) is not int for value in row["point"])
            or type(row.get("label")) is not int
        ):
            raise FairChoiceError("public D0 row is malformed")
        normalized_d0.append(
            {"point": list(row["point"]), "label": int(row["label"])}
        )
    return {
        "D0": normalized_d0,
        "parent": parent,
        "old_subtrees": {
            "LEFT": old_by_path[(1, 1)],
            "RIGHT": old_by_path[(1, 2)],
        },
    }


def _assert_support_world_matches_context(
    world: SparkWorld, context: Mapping[str, Any]
) -> None:
    parent = spark_lineage.select_parent(world)
    observed_d0 = [
        {"point": list(example.point), "label": example.label}
        for example in world.train
    ]
    if dsl.to_sexpr(parent) != context.get("parent") or observed_d0 != context.get("D0"):
        raise FairChoiceError("target-free K1 world differs from sealed public D0/parent")


def k1_support_profiles_for_world(world: SparkWorld) -> tuple[K1SupportProfile, ...]:
    """Compute all motif K1 masks; target/K2--K4 are neither needed nor read."""

    if not isinstance(world, SparkWorld):
        raise TypeError("world must be a SparkWorld")
    lineages = spark_lineage.enumerate_reachable_children(world)
    indexed = strong_scan._lineage_index(lineages)
    profiles: list[K1SupportProfile] = []
    for motif in spark_lineage.build_motif_library():
        actions = strong_scan._raw_actions(world, motif.motif_id)
        if tuple(_parsed_edit_action(action) for action in actions) != tuple(
            _semantic_action(index) for index in range(RAW_ACTION_COUNT)
        ):
            raise FairChoiceError("raw action order drifted from fair-choice semantics")
        profiles.append(
            K1SupportProfile(
                motif_id=motif.motif_id,
                motif_sexpr=dsl.to_sexpr(motif.ast),
                motif_canonical_hash=motif.canonical_hash,
                motif_behavior_hash=dsl.behavior_hash(motif.ast),
                stratum=motif.stratum,
                complexity_bucket=motif.complexity_bucket,
                k1_mask=tuple(action in indexed for action in actions),
            )
        )
    return tuple(profiles)


def build_target_free_k1_profiles(
    world_seed: int, public_context: Mapping[str, Any]
) -> tuple[K1SupportProfile, ...]:
    world = _target_free_support_world(world_seed)
    _assert_support_world_matches_context(world, public_context)
    return k1_support_profiles_for_world(world)


def _hamming(left: Sequence[bool], right: Sequence[bool]) -> int:
    if len(left) != RAW_ACTION_COUNT or len(right) != RAW_ACTION_COUNT:
        raise FairChoiceError("K1 Hamming masks must each contain ten bits")
    return sum(a != b for a, b in zip(left, right, strict=True))


def _alias_representative_sha256(
    pair_anchor: str, candidate: K1SupportProfile
) -> str:
    return _sha256_json(
        {
            "namespace": SHAM_TIE_NAMESPACE,
            "phase": "alias-representative",
            "pair_anchor_sha256": pair_anchor,
            "motif_behavior_hash": candidate.motif_behavior_hash,
            "motif_canonical_hash": candidate.motif_canonical_hash,
        }
    )


def _behavior_tie_sha256(
    pair_anchor: str, representative: K1SupportProfile
) -> str:
    return _sha256_json(
        {
            "namespace": SHAM_TIE_NAMESPACE,
            "phase": "behavior-tie",
            "pair_anchor_sha256": pair_anchor,
            "motif_behavior_hash": representative.motif_behavior_hash,
            "representative_canonical_hash": (
                representative.motif_canonical_hash
            ),
        }
    )


def select_matched_sham(
    factual_motif_id: str,
    profiles: Sequence[K1SupportProfile],
    pair_anchor: str,
) -> tuple[K1SupportProfile, dict[str, Any]]:
    """Choose a behavior-balanced K1 match with the exact frozen hash rules."""

    if not _is_sha256(pair_anchor):
        raise FairChoiceError("sham selection requires the frozen pair anchor")
    if not isinstance(profiles, (list, tuple)) or not profiles:
        raise FairChoiceError("sham selection requires K1 support profiles")
    matches = [profile for profile in profiles if profile.motif_id == factual_motif_id]
    if len(matches) != 1:
        raise FairChoiceError("factual motif does not identify one K1 profile")
    factual = matches[0]
    eligible_aliases = [
        profile
        for profile in profiles
        if profile.motif_id != factual.motif_id
        and profile.stratum == factual.stratum
        and profile.complexity_bucket == factual.complexity_bucket
        and profile.motif_behavior_hash != factual.motif_behavior_hash
    ]
    if not eligible_aliases:
        raise FairChoiceError("factual motif has no same-stratum/complexity sham")

    by_behavior: dict[str, list[K1SupportProfile]] = {}
    for profile in eligible_aliases:
        by_behavior.setdefault(profile.motif_behavior_hash, []).append(profile)
    representatives = [
        min(
            aliases,
            key=lambda candidate: (
                _alias_representative_sha256(pair_anchor, candidate),
                candidate.motif_id,
            ),
        )
        for _behavior, aliases in sorted(by_behavior.items())
    ]
    ranked = [
        (
            _hamming(factual.k1_mask, representative.k1_mask),
            _behavior_tie_sha256(pair_anchor, representative),
            representative.motif_behavior_hash,
            representative.motif_id,
            representative,
        )
        for representative in representatives
    ]
    distance, tie_digest, _behavior, _motif_id, selected = min(
        ranked, key=lambda row: row[:4]
    )
    minimum = min(row[0] for row in ranked)
    if distance != minimum:
        raise AssertionError("unreachable non-minimal sham selection")
    eligible_commitment = _sha256_json(
        [
            {
                "motif_sexpr": candidate.motif_sexpr,
                "motif_behavior_hash": candidate.motif_behavior_hash,
                "representative_canonical_hash": candidate.motif_canonical_hash,
                "k1_mask": list(candidate.k1_mask),
                "hamming_distance": candidate_distance,
                "tie_sha256": candidate_digest,
            }
            for candidate_distance, candidate_digest, _behavior, _motif_id, candidate in sorted(
                ranked, key=lambda row: row[4].motif_sexpr
            )
        ]
    )
    audit = {
        "selection_inputs": [
            "public_D0",
            "public_parent",
            "complete_input_domain",
            "public_evidence_and_test_point_locations",
            "motif_stratum",
            "motif_complexity_bucket",
            "ten_bit_K1_support_mask",
        ],
        "target_or_K2_K3_K4_read": False,
        "same_stratum_and_complexity_required": True,
        "factual_excluded": True,
        "all_factual_behavior_aliases_excluded": True,
        "eligible_alias_count": len(eligible_aliases),
        "eligible_behavior_group_count": len(representatives),
        "minimum_K1_mask_hamming_distance": minimum,
        "selected_tie_sha256": tie_digest,
        "selected_motif_behavior_hash": selected.motif_behavior_hash,
        "selected_representative_canonical_hash": selected.motif_canonical_hash,
        "eligible_candidate_commitment_sha256": eligible_commitment,
    }
    return selected, audit


def render_fair_choice_prompt(
    public_context: Mapping[str, Any],
    spark_sexpr: str,
    action_order: Sequence[int],
    option_ids: Sequence[str],
) -> str:
    """Render one provider-facing prompt from an intentionally tiny schema."""

    if set(public_context) != {"D0", "parent", "old_subtrees"}:
        raise FairChoiceError("prompt renderer accepts only the public context schema")
    if not isinstance(spark_sexpr, str) or not spark_sexpr:
        raise FairChoiceError("spark expression must be non-empty")
    if (
        not isinstance(action_order, (list, tuple))
        or len(action_order) != RAW_ACTION_COUNT
        or set(action_order) != set(range(RAW_ACTION_COUNT))
    ):
        raise FairChoiceError("prompt action order must permute all ten actions")
    if (
        not isinstance(option_ids, (list, tuple))
        or len(option_ids) != RAW_ACTION_COUNT
        or len(set(option_ids)) != RAW_ACTION_COUNT
        or any(
            not isinstance(option_id, str)
            or re.fullmatch(r"Q[0-9A-F]{8}", option_id) is None
            for option_id in option_ids
        )
    ):
        raise FairChoiceError("prompt requires ten canonical opaque option IDs")
    d0 = public_context["D0"]
    if not isinstance(d0, list):
        raise FairChoiceError("prompt public context is malformed")
    rendered_d0 = "\n".join(
        f"  {tuple(row['point'])} -> {row['label']}" for row in d0
    )
    options = "\n".join(
        f"  {option_ids[position]}: {_action_description(raw_action_index)}"
        for position, raw_action_index in enumerate(action_order)
    )
    prompt = (
        "Choose one replayable edit for a finite symbolic binary classifier.\n"
        "Return exactly one JSON object with the single key expression. Its value "
        "must be one listed opaque option ID. Do not return prose, markdown, a "
        "program, or any additional key.\n\n"
        f"Public observations:\n{rendered_d0}\n\n"
        "The complete input domain has x1, x2, and x3 each in "
        "{-2,-1,0,1,2}; a legal result must output only 0 or 1 everywhere.\n\n"
        f"Frozen parent:\n{public_context['parent']}\n\n"
        f"Context fragment:\n{spark_sexpr}\n\n"
        "In each choice, OLD means the current subtree at the named predicate "
        "operand. CONTEXT means the context fragment above.\n\n"
        "Options:\n"
        f"{options}\n\n"
        "Choose the edit whose resulting classifier is binary, remains consistent "
        "with every public observation, and is most likely to distinguish the "
        "unknown rule during the fixed four-round verification process. Use CONTEXT "
        "exactly once.\n\n"
        'Required output schema: {"expression":"<OPTION_ID>"}\n'
    )
    _validate_rendered_prompt(prompt, option_ids)
    return prompt


def _validate_rendered_prompt(
    prompt: str, option_ids: Sequence[str] | None = None
) -> None:
    if not isinstance(prompt, str) or not prompt:
        raise FairChoiceError("rendered prompt must be non-empty")
    lowered = prompt.lower()
    leaked = [
        term
        for term in _PROMPT_FORBIDDEN_TERMS
        if re.search(
            rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])", lowered
        )
    ]
    if leaked:
        raise FairChoiceError(f"rendered prompt contains forbidden metadata: {leaked}")
    if _LONG_INTEGER_RE.search(prompt) or _SHA256_RE.search(prompt):
        raise FairChoiceError("rendered prompt contains a seed/index/hash-like token")
    option_lines = [
        line.strip()
        for line in prompt.splitlines()
        if re.fullmatch(
            r"  Q[0-9A-F]{8}: location=PREDICATE_(LEFT|RIGHT); "
            r"new_subtree=.+",
            line,
        )
    ]
    if len(option_lines) != RAW_ACTION_COUNT:
        raise FairChoiceError("rendered prompt must list exactly ten opaque options")
    observed_ids = [line.split(":", 1)[0] for line in option_lines]
    expected_ids = list(option_ids) if option_ids is not None else observed_ids
    if len(set(observed_ids)) != RAW_ACTION_COUNT or observed_ids != expected_ids:
        raise FairChoiceError("rendered prompt opaque option IDs drifted")
    for option_id in expected_ids:
        if sum(line.startswith(f"{option_id}:") for line in option_lines) != 1:
            raise FairChoiceError("rendered prompt option ids are missing or duplicated")
    if "(edit replace 1 1)" in prompt or any(
        f'"expression":"{option_id}"' in prompt for option_id in observed_ids
    ):
        raise FairChoiceError("rendered prompt contains a concrete answer example")


def _action_public_features(
    world: SparkWorld,
    lineage: LineageRecord | None,
) -> dict[str, Any]:
    if lineage is None:
        return {
            "K1_supported": False,
            "full_domain_positive_count": None,
            "node_count": None,
            "child_canonical_hash": None,
            "child_behavior_hash": None,
            "parent_behavior_novelty_count": None,
            "child_behavior_is_constant": None,
        }
    behavior = dsl.behavior_vector(lineage.child_ast, world.domain)
    if set(behavior) - {0, 1}:
        raise FairChoiceError("K1 lineage child is not binary")
    parent_behavior = dsl.behavior_vector(lineage.parent_ast, world.domain)
    return {
        "K1_supported": True,
        "full_domain_positive_count": sum(behavior),
        "node_count": dsl.node_count(lineage.child_ast),
        "child_canonical_hash": lineage.child_canonical_hash,
        "child_behavior_hash": lineage.child_behavior_hash,
        "parent_behavior_novelty_count": sum(
            child != parent
            for child, parent in zip(behavior, parent_behavior, strict=True)
        ),
        "child_behavior_is_constant": len(set(behavior)) == 1,
    }


def _distill_action_row(
    world: SparkWorld,
    row: Mapping[str, Any],
    lineage: LineageRecord | None,
    *,
    option_id: str,
) -> dict[str, Any]:
    raw_index = row.get("raw_action_index")
    flags = row.get("endpoint_flags")
    if (
        type(raw_index) is not int
        or not 0 <= raw_index < RAW_ACTION_COUNT
        or not isinstance(flags, Mapping)
        or set(flags) != set(ENDPOINT_NAMES)
        or any(type(flags[name]) is not bool for name in ENDPOINT_NAMES)
    ):
        raise FairChoiceError("replayed action endpoint row is malformed")
    if not (
        (not flags["K2"] or flags["K1"])
        and (not flags["K3"] or flags["K2"])
        and (not flags["K4_full_pool"] or flags["K3"])
    ):
        raise FairChoiceError("replayed strong endpoints are not nested")
    features = _action_public_features(world, lineage)
    if features["K1_supported"] != flags["K1"]:
        raise FairChoiceError("public K1 features disagree with replayed K1 endpoint")
    if flags["K2"] != flags["K3"]:
        raise FairChoiceError(
            "selected cohort parent must make symmetric usefulness K2 equal K3"
        )
    return {
        "raw_action_index": raw_index,
        "option_id": option_id,
        "semantic_action": _semantic_action(raw_index).to_dict(),
        "public_features": features,
        "endpoint_flags": {name: bool(flags[name]) for name in ENDPOINT_NAMES},
        "full_pool_counterfactual_bundle_sha256": row.get(
            "full_pool_counterfactual_bundle_sha256"
        ),
    }


def _replay_pair_outcomes(
    world_record: Mapping[str, Any],
    factual_slot_id: str,
    factual_motif_id: str,
    sham_motif_id: str,
    option_to_raw_action: Mapping[str, int],
) -> dict[str, list[dict[str, Any]]]:
    """Replay the same ten scientific endpoints for both pair arms."""

    world_seed = world_record.get("world_seed")
    if type(world_seed) is not int:
        raise FairChoiceError("private world binding lacks an integer world seed")
    config = {
        "private_target_and_public_motif_namespaces": {
            "target_seed_namespace": SEALED_SCAN_PROTOCOL_ID
        }
    }
    world = strong_scan.materialize_private_candidate_world(config, world_seed)
    parent = spark_lineage.select_parent(world)
    public = world_record.get("public_identity")
    if not isinstance(public, Mapping) or not isinstance(public.get("world"), Mapping):
        raise FairChoiceError("private world lacks its sealed public identity")
    context = _public_context_from_world_entry(public["world"])
    _assert_support_world_matches_context(world, context)
    if dsl.canonical_hash(parent) != world_record["private_outcome"][
        "parent_canonical_hash"
    ]:
        raise FairChoiceError("private replay parent differs from sealed scan")

    compressor = strong_scan._BehaviorCachedCompressor(SparkCompressor(world))
    parent_result = compressor.run(
        parent, max_rounds=spark_closure.CLOSURE_MAX_ROUNDS
    )
    if parent_result.exact_identification:
        raise FairChoiceError("strong-K4 cohort parent unexpectedly reaches endpoint")
    lineages = spark_lineage.enumerate_reachable_children(world)
    indexed = strong_scan._lineage_index(lineages)
    raw_to_option = {raw: option for option, raw in option_to_raw_action.items()}
    if set(raw_to_option) != set(range(RAW_ACTION_COUNT)):
        raise FairChoiceError("option mapping is not a ten-action bijection")

    replayed: dict[str, list[dict[str, Any]]] = {}
    full_rows_by_arm: dict[str, list[dict[str, Any]]] = {}
    for arm, motif_id in (("factual", factual_motif_id), ("sham", sham_motif_id)):
        actions = strong_scan._raw_actions(world, motif_id)
        rows = [
            strong_scan._analyze_action(
                raw_action_index=raw_index,
                action=action,
                lineage=indexed.get(action),
                compressor=compressor,
                parent_result=parent_result,
                minimum_controls=MINIMUM_UNIQUE_CONTROL_BEHAVIORS,
            )
            for raw_index, action in enumerate(actions)
        ]
        full_rows_by_arm[arm] = rows
        replayed[arm] = [
            _distill_action_row(
                world,
                row,
                indexed.get(action),
                option_id=raw_to_option[raw_index],
            )
            for raw_index, (action, row) in enumerate(
                zip(actions, rows, strict=True)
            )
        ]

    frozen_slots = world_record.get("private_outcome", {}).get("slots")
    if not isinstance(frozen_slots, list):
        raise FairChoiceError("sealed private slots are malformed")
    matches = [slot for slot in frozen_slots if slot.get("slot_id") == factual_slot_id]
    if len(matches) != 1 or matches[0].get("motif_id") != factual_motif_id:
        raise FairChoiceError("factual witness does not match the sealed private slot")
    frozen_actions = matches[0].get("actions")
    if not isinstance(frozen_actions, list) or len(frozen_actions) != RAW_ACTION_COUNT:
        raise FairChoiceError("sealed factual slot lacks ten actions")
    for frozen, observed in zip(
        frozen_actions, full_rows_by_arm["factual"], strict=True
    ):
        for field in (
            "raw_action_index",
            "action",
            "endpoint_flags",
            "child_canonical_hash",
            "child_behavior_hash",
            "full_pool_counterfactual_bundle_sha256",
        ):
            if frozen.get(field) != observed.get(field):
                raise FairChoiceError(
                    f"factual replay differs from sealed scan field {field}"
                )
    return replayed


def _motif_payload(profile: K1SupportProfile) -> dict[str, Any]:
    motif = spark_lineage.motif_by_id(profile.motif_id)
    if (
        dsl.to_sexpr(motif.ast) != profile.motif_sexpr
        or motif.canonical_hash != profile.motif_canonical_hash
        or dsl.behavior_hash(motif.ast) != profile.motif_behavior_hash
        or motif.stratum != profile.stratum
        or motif.complexity_bucket != profile.complexity_bucket
    ):
        raise FairChoiceError("K1 profile differs from frozen motif library")
    return {
        "motif_id": motif.motif_id,
        "motif_sexpr": profile.motif_sexpr,
        "motif_canonical_hash": motif.canonical_hash,
        "motif_behavior_hash": profile.motif_behavior_hash,
        "stratum": motif.stratum,
        "complexity_bucket": list(motif.complexity_bucket),
        "K1_support_mask": list(profile.k1_mask),
    }


def _task_record(task_id: str, prompt: str) -> dict[str, str]:
    return {
        "task_id": task_id,
        "rendered_prompt": prompt,
        "prompt_sha256": _sha256_text(prompt),
    }


def _select_baseline_raw_action(
    policy_id: str,
    actions: Sequence[Mapping[str, Any]],
    action_order: Sequence[int],
) -> int | None:
    """Select using raw identity or target-free K1 child features only."""

    if policy_id in FIXED_RAW_POLICY_IDS:
        return int(policy_id.rsplit("-", 1)[1])
    if policy_id in FIXED_DISPLAY_POLICY_IDS:
        position = int(policy_id.rsplit("-", 1)[1])
        return int(action_order[position])
    if policy_id not in PUBLIC_K1_POLICY_IDS:
        raise FairChoiceError(f"unknown frozen baseline policy: {policy_id!r}")
    supported = [
        action
        for action in actions
        if isinstance(action.get("public_features"), Mapping)
        and action["public_features"].get("K1_supported") is True
    ]
    if not supported:
        return int(action_order[0])

    if policy_id == "first-public-K1-else-first-displayed":
        supported_raw = {
            int(action["raw_action_index"]) for action in supported
        }
        return next(int(raw) for raw in action_order if raw in supported_raw)

    def rank(action: Mapping[str, Any]) -> tuple[Any, ...]:
        features = action["public_features"]
        positive = features["full_domain_positive_count"]
        nodes = features["node_count"]
        child_hash = features["child_canonical_hash"]
        novelty = features["parent_behavior_novelty_count"]
        raw = action["raw_action_index"]
        if (
            type(positive) is not int
            or type(nodes) is not int
            or not _is_sha256(child_hash)
            or type(novelty) is not int
            or type(raw) is not int
        ):
            raise FairChoiceError("public K1 baseline features are malformed")
        if policy_id == "public-k1-min-positive-node-hash":
            return positive, nodes, child_hash, raw
        if policy_id == "public-k1-min-node-hash":
            return nodes, child_hash, raw
        return -novelty, nodes, child_hash, raw

    return int(min(supported, key=rank)["raw_action_index"])


def exact_one_sided_mcnemar(
    left: Sequence[bool], right: Sequence[bool]
) -> dict[str, Any]:
    """Exact one-sided paired sign/McNemar test for left > right."""

    if len(left) != len(right) or any(type(value) is not bool for value in (*left, *right)):
        raise FairChoiceError("paired endpoint vectors must be equal-length booleans")
    left_only = sum(a and not b for a, b in zip(left, right, strict=True))
    right_only = sum(b and not a for a, b in zip(left, right, strict=True))
    discordant = left_only + right_only
    if discordant == 0:
        tail = Fraction(1, 1)
    else:
        tail = sum(
            Fraction(_comb(discordant, successes), 2**discordant)
            for successes in range(left_only, discordant + 1)
        )
    return {
        "pair_count": len(left),
        "left_success_count": sum(left),
        "right_success_count": sum(right),
        "left_only_count": left_only,
        "right_only_count": right_only,
        "discordant_pair_count": discordant,
        "alternative": "left_greater_than_right",
        "exact_one_sided_p_value": _fraction_payload(tail),
    }


def _comb(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    k = min(k, n - k)
    result = 1
    for value in range(1, k + 1):
        result = result * (n - k + value) // value
    return result


def poisson_binomial_tail(
    qualifying_action_counts: Sequence[int],
    observed_successes: int,
    *,
    choice_count: int = RAW_ACTION_COUNT,
) -> Fraction:
    """Exact uniform-choice tail with non-identical per-pair success rates."""

    if type(choice_count) is not int or choice_count < 1:
        raise FairChoiceError("choice_count must be a positive integer")
    counts = tuple(qualifying_action_counts)
    if (
        any(type(value) is not int or not 0 <= value <= choice_count for value in counts)
        or type(observed_successes) is not int
        or not 0 <= observed_successes <= len(counts) + 1
    ):
        raise FairChoiceError("Poisson-binomial inputs are malformed")
    distribution = [Fraction(1, 1)]
    for count in counts:
        probability = Fraction(count, choice_count)
        updated = [Fraction(0, 1)] * (len(distribution) + 1)
        for successes, mass in enumerate(distribution):
            updated[successes] += mass * (1 - probability)
            updated[successes + 1] += mass * probability
        distribution = updated
    if observed_successes > len(counts):
        return Fraction(0, 1)
    return sum(distribution[observed_successes:], Fraction(0, 1))


def poisson_binomial_critical_value(
    qualifying_action_counts: Sequence[int],
    *,
    alpha: Fraction = Fraction(1, 20),
    choice_count: int = RAW_ACTION_COUNT,
) -> int:
    """Smallest count whose upper tail is <= alpha; n+1 means unattainable."""

    if not isinstance(alpha, Fraction) or not 0 < alpha < 1:
        raise FairChoiceError("alpha must be a Fraction strictly between zero and one")
    counts = tuple(qualifying_action_counts)
    for successes in range(len(counts) + 2):
        if poisson_binomial_tail(
            counts, successes, choice_count=choice_count
        ) <= alpha:
            return successes
    raise AssertionError("unreachable Poisson-binomial critical-value search")


def holm_adjusted_route_decisions(
    route_p_values: Mapping[str, Fraction],
    *,
    alpha: Fraction = Fraction(1, 20),
) -> dict[str, dict[str, Any]]:
    """Return exact Holm adjusted p-values and decisions for a route family."""

    if not isinstance(route_p_values, Mapping) or not route_p_values:
        raise FairChoiceError("Holm adjustment requires a non-empty route mapping")
    if not isinstance(alpha, Fraction) or not 0 < alpha < 1:
        raise FairChoiceError("Holm alpha must be an exact Fraction in (0, 1)")
    normalized: dict[str, Fraction] = {}
    for route_id, p_value in route_p_values.items():
        if (
            not isinstance(route_id, str)
            or not route_id
            or not isinstance(p_value, Fraction)
            or not 0 <= p_value <= 1
        ):
            raise FairChoiceError("Holm route ids/p-values are malformed")
        normalized[route_id] = p_value

    ordered = sorted(normalized.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, Fraction] = {}
    running = Fraction(0, 1)
    family_size = len(ordered)
    for rank, (route_id, p_value) in enumerate(ordered):
        candidate = min(Fraction(1, 1), (family_size - rank) * p_value)
        running = max(running, candidate)
        adjusted[route_id] = running
    return {
        route_id: {
            "raw_p_value": normalized[route_id],
            "adjusted_p_value": adjusted[route_id],
            "rejected": adjusted[route_id] <= alpha,
        }
        for route_id in sorted(normalized)
    }


def _fraction_from_payload(value: object, label: str) -> Fraction:
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, Mapping):
        numerator = value.get("numerator")
        denominator = value.get("denominator")
        if (
            type(numerator) is not int
            or type(denominator) is not int
            or denominator <= 0
        ):
            raise FairChoiceError(f"{label} exact p-value payload is malformed")
        result = Fraction(numerator, denominator)
    else:
        raise FairChoiceError(f"{label} exact p-value payload is malformed")
    if not 0 <= result <= 1:
        raise FairChoiceError(f"{label} p-value is outside [0, 1]")
    return result


def _joint_non_evaluable() -> dict[str, Any]:
    return {
        "joint_classification": "non_evaluable_incomplete_attempt",
        "route_classifications": {},
        "holm": {},
    }


def classify_joint_routes(
    scores_by_route: Mapping[str, Mapping[str, Any]],
    *,
    alpha: Fraction = Fraction(1, 20),
) -> dict[str, Any]:
    """Apply frozen per-route gates, exact Holm, and the closed joint labels."""

    if not isinstance(scores_by_route, Mapping):
        raise FairChoiceError("joint route scores must be a mapping")
    if set(scores_by_route) != set(CANONICAL_ROUTE_IDS):
        return _joint_non_evaluable()
    if not isinstance(alpha, Fraction) or not 0 < alpha < 1:
        raise FairChoiceError("joint alpha must be an exact Fraction in (0, 1)")

    inputs: dict[str, dict[str, Any]] = {}
    shared_identity: tuple[str, str, str] | None = None
    for route_id in CANONICAL_ROUTE_IDS:
        score = scores_by_route[route_id]
        if (
            not isinstance(score, Mapping)
        ):
            raise FairChoiceError(f"route score {route_id!r} must be an object")
        unsigned_score = {
            key: value for key, value in score.items() if key != "score_sha256"
        }
        identity = (
            score.get("public_manifest_sha256"),
            score.get("private_key_sha256"),
            score.get("current_source_manifest_sha256"),
        )
        if (
            score.get("schema_version") != SCHEMA_VERSION
            or score.get("kind") != OFFLINE_SCORE_KIND
            or score.get("protocol_id") != PROTOCOL_ID
            or score.get("model_id") != route_id
            or score.get("received_response_count") != TASK_COUNT
            or any(not _is_sha256(value) for value in identity)
            or score.get("score_sha256") != _sha256_json(unsigned_score)
        ):
            raise FairChoiceError(f"route score {route_id!r} identity is malformed")
        typed_identity = (str(identity[0]), str(identity[1]), str(identity[2]))
        if shared_identity is None:
            shared_identity = typed_identity
        elif typed_identity != shared_identity:
            raise FairChoiceError("route scores do not share one sealed benchmark")
        try:
            paired = score["pair_primary_U"]
            shortcut = score["versus_frozen_B_star"][
                "paired_test_model_greater_B_star"
            ]
            uniform = score["versus_uniform_choice"]
            breadth = score["factual_strong_breadth"]
            p_pair = _fraction_from_payload(
                paired["exact_one_sided_p_value"], "paired"
            )
            p_shortcut = _fraction_from_payload(
                shortcut["exact_one_sided_p_value"], "shortcut"
            )
            p_uniform = _fraction_from_payload(
                uniform["exact_Poisson_binomial_upper_tail"], "uniform"
            )
            paired_direction = (
                int(paired["left_only_count"]) > int(paired["right_only_count"])
            )
            shortcut_direction = (
                int(shortcut["left_only_count"])
                > int(shortcut["right_only_count"])
            )
            stratum_count = int(breadth["construction_stratum_count"])
            behavior_count = int(breadth["unique_child_behavior_count"])
            nonconstant_count = int(breadth["nonconstant_child_hit_count"])
            factual_f_count = int(score["factual_strong_F_count"])
            invalid_count = int(score["invalid_response_count"])
            critical_reached = bool(uniform["exceeds_or_meets_critical_value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FairChoiceError(
                f"route score {route_id!r} lacks frozen gate inputs"
            ) from exc
        inputs[route_id] = {
            "p_pair": p_pair,
            "p_shortcut": p_shortcut,
            "p_route": max(p_pair, p_shortcut),
            "p_uniform": p_uniform,
            "paired_direction": paired_direction,
            "shortcut_direction": shortcut_direction,
            "construction_stratum_count": stratum_count,
            "unique_child_behavior_count": behavior_count,
            "nonconstant_child_hit_count": nonconstant_count,
            "factual_strong_F_count": factual_f_count,
            "invalid_response_count": invalid_count,
            "uniform_critical_reached": critical_reached,
        }

    holm = holm_adjusted_route_decisions(
        {route_id: row["p_route"] for route_id, row in inputs.items()},
        alpha=alpha,
    )
    route_classifications: dict[str, dict[str, Any]] = {}
    positive_routes: set[str] = set()
    for route_id in CANONICAL_ROUTE_IDS:
        row = inputs[route_id]
        gates = {
            "paired_direction": row["paired_direction"],
            "shortcut_direction": row["shortcut_direction"],
            "holm_adjusted_p_route_at_most_alpha": holm[route_id]["rejected"],
            "uniform_upper_tail_p_at_most_alpha": row["p_uniform"] <= alpha,
            "uniform_critical_count_reached": row["uniform_critical_reached"],
            "minimum_two_construction_strata": (
                row["construction_stratum_count"] >= 2
            ),
            "minimum_two_child_behavior_hashes": (
                row["unique_child_behavior_count"] >= 2
            ),
            "minimum_one_nonconstant_child_behavior": (
                row["nonconstant_child_hit_count"] >= 1
            ),
        }
        positive = all(gates.values())
        if positive:
            classification = "paired_strong_K4_effect_observed"
            positive_routes.add(route_id)
        elif row["invalid_response_count"] == TASK_COUNT:
            classification = "model_dsl_interface_failure"
        elif row["factual_strong_F_count"] > 0:
            classification = "strong_hits_shortcut_compatible"
        else:
            classification = "effect_not_observed"
        route_classifications[route_id] = {
            "classification": classification,
            "positive": positive,
            "gates": gates,
            "p_pair": row["p_pair"],
            "p_shortcut": row["p_shortcut"],
            "p_route": row["p_route"],
            "p_uniform": row["p_uniform"],
            "holm_adjusted_p_route": holm[route_id]["adjusted_p_value"],
        }

    if positive_routes == set(CANONICAL_ROUTE_IDS):
        joint = "all_routes_effect_observed"
    elif "glm-5.2" in positive_routes and positive_routes.intersection(
        {"deepseek-flash", "deepseek-pro"}
    ):
        joint = "cross_family_effect_observed"
    elif positive_routes == {"deepseek-flash", "deepseek-pro"}:
        joint = "deepseek_family_only_effect_observed"
    elif len(positive_routes) == 1:
        joint = "single_route_effect_observed"
    else:
        joint = "effect_not_observed_under_frozen_protocol"
    return {
        "joint_classification": joint,
        "route_classifications": route_classifications,
        "holm": holm,
    }


def _action_by_raw(
    actions: Sequence[Mapping[str, Any]], raw_action_index: int | None
) -> Mapping[str, Any] | None:
    if raw_action_index is None:
        return None
    matches = [
        action
        for action in actions
        if action.get("raw_action_index") == raw_action_index
    ]
    if len(matches) != 1:
        raise FairChoiceError("raw action does not identify one private outcome")
    return matches[0]


def build_baseline_report(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Freeze all public shortcut baselines and select endpoint-best B*."""

    if len(pairs) != PAIR_COUNT:
        raise FairChoiceError("baseline report requires exactly 32 pairs")
    policies: list[dict[str, Any]] = []
    for policy_id in BASELINE_POLICY_IDS:
        factual_f: list[bool] = []
        factual_u: list[bool] = []
        sham_u: list[bool] = []
        selected_raw_by_pair: list[dict[str, Any]] = []
        for pair in pairs:
            arms = pair["arms"]
            selected: dict[str, int | None] = {}
            outcomes: dict[str, Mapping[str, Any] | None] = {}
            for arm in ARMS:
                raw = _select_baseline_raw_action(
                    policy_id,
                    arms[arm]["actions"],
                    pair["action_order"],
                )
                selected[arm] = raw
                outcomes[arm] = _action_by_raw(arms[arm]["actions"], raw)
            factual = outcomes["factual"]
            sham = outcomes["sham"]
            factual_f.append(
                bool(factual and factual["endpoint_flags"]["K4_full_pool"])
            )
            factual_u.append(bool(factual and factual["endpoint_flags"]["K2"]))
            sham_u.append(bool(sham and sham["endpoint_flags"]["K2"]))
            selected_raw_by_pair.append(
                {
                    "pair_id": pair["pair_id"],
                    "factual": selected["factual"],
                    "sham": selected["sham"],
                }
            )
        constant_hits = sum(
            hit
            and bool(
                _action_by_raw(
                    pair["arms"]["factual"]["actions"],
                    selected_raw_by_pair[index]["factual"],
                )["public_features"]["child_behavior_is_constant"]
            )
            for index, (pair, hit) in enumerate(zip(pairs, factual_f, strict=True))
        )
        policies.append(
            {
                "policy_id": policy_id,
                "selection_reads_endpoint_outcomes": False,
                "factual_F_by_pair": factual_f,
                "factual_F_count": sum(factual_f),
                "factual_F_constant_child_count": constant_hits,
                "factual_F_nonconstant_child_count": sum(factual_f)
                - constant_hits,
                "paired_U": exact_one_sided_mcnemar(factual_u, sham_u),
                "selected_raw_action_by_pair": selected_raw_by_pair,
            }
        )
    best = min(
        policies,
        key=lambda row: (-int(row["factual_F_count"]), str(row["policy_id"])),
    )
    qualifying_counts = [
        sum(
            bool(action["endpoint_flags"]["K4_full_pool"])
            for action in pair["arms"]["factual"]["actions"]
        )
        for pair in pairs
    ]
    critical = poisson_binomial_critical_value(qualifying_counts)
    return {
        "frozen_policy_ids": list(BASELINE_POLICY_IDS),
        "policy_count": len(BASELINE_POLICY_IDS),
        "policy_selection_information": (
            "semantic identity, display position, or target-blind structural K1 "
            "child features only"
        ),
        "B_star_selection_endpoint": "factual_K4_full_pool_F",
        "B_star_selected_before_model_calls": True,
        "B_star_tie_break": "lexicographically_smallest_frozen_policy_id",
        "B_star_policy_id": best["policy_id"],
        "B_star_factual_F_count": best["factual_F_count"],
        "B_star_factual_F_by_pair": list(best["factual_F_by_pair"]),
        "policies": policies,
        "uniform_choice": {
            "choices_per_pair": RAW_ACTION_COUNT,
            "factual_qualifying_action_counts": qualifying_counts,
            "alpha": _fraction_payload(Fraction(1, 20)),
            "critical_factual_F_count": critical,
            "critical_value_attainable": critical <= len(qualifying_counts),
        },
    }


def _construction_audit(pairs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    distances = Counter(
        int(pair["sham_selection_audit"]["minimum_K1_mask_hamming_distance"])
        for pair in pairs
    )
    factual_frames = 0
    sham_k1_supported = 0
    all_sham_k1_supported_worlds = 0
    sham_k2_failed_among_k1_supported = 0
    sham_k2_failed_with_invalid_as_miss = 0
    all_sham_k2_failed_worlds = 0
    for pair in pairs:
        factual = pair["arms"]["factual"]["actions"]
        sham_by_raw = {
            int(action["raw_action_index"]): action
            for action in pair["arms"]["sham"]["actions"]
        }
        qualifying = [
            action
            for action in factual
            if action["endpoint_flags"]["K4_full_pool"]
        ]
        factual_frames += len(qualifying)
        world_k1_supported = 0
        world_k2_failures = 0
        for action in qualifying:
            sham = sham_by_raw[int(action["raw_action_index"])]
            k1_supported = bool(sham["endpoint_flags"]["K1"])
            k2_failed = not bool(sham["endpoint_flags"]["K2"])
            sham_k1_supported += k1_supported
            world_k1_supported += k1_supported
            sham_k2_failed_among_k1_supported += k1_supported and k2_failed
            # A K1-invalid same-frame replacement is an endpoint miss, but it
            # is kept distinct from a valid replacement that fails closure.
            sham_k2_failed_with_invalid_as_miss += k2_failed
            world_k2_failures += k2_failed
        all_sham_k1_supported_worlds += (
            bool(qualifying) and world_k1_supported == len(qualifying)
        )
        all_sham_k2_failed_worlds += (
            bool(qualifying) and world_k2_failures == len(qualifying)
        )
    observed = {
        "minimum_hamming_distance_histogram": {
            str(distance): distances.get(distance, 0)
            for distance in sorted(
                set(distances).union(EXPECTED_SHAM_HAMMING_COUNTS)
            )
        },
        "factual_K4_frame_count": factual_frames,
        "same_frame_sham_K1_supported_count": sham_k1_supported,
        "worlds_with_all_factual_K4_frames_sham_K1_supported": (
            all_sham_k1_supported_worlds
        ),
        "same_frame_sham_K2_failure_among_K1_supported": (
            sham_k2_failed_among_k1_supported
        ),
        "same_frame_sham_K2_failure_with_K1_invalid_counted_as_miss": (
            sham_k2_failed_with_invalid_as_miss
        ),
        "worlds_with_all_factual_K4_frames_sham_K2_failure": (
            all_sham_k2_failed_worlds
        ),
    }
    expected = {
        "minimum_hamming_distance_histogram": {
            str(distance): count
            for distance, count in EXPECTED_SHAM_HAMMING_COUNTS.items()
        },
        "factual_K4_frame_count": EXPECTED_FACTUAL_K4_FRAME_COUNT,
        "same_frame_sham_K1_supported_count": (
            EXPECTED_FACTUAL_K4_FRAMES_SHAM_K1_SUPPORTED
        ),
        "worlds_with_all_factual_K4_frames_sham_K1_supported": (
            EXPECTED_ALL_FACTUAL_K4_FRAMES_SHAM_K1_SUPPORTED_WORLD_COUNT
        ),
        "same_frame_sham_K2_failure_among_K1_supported": (
            EXPECTED_SHAM_K2_FAILURE_AMONG_K1_SUPPORTED
        ),
        "same_frame_sham_K2_failure_with_K1_invalid_counted_as_miss": (
            EXPECTED_SHAM_K2_FAILURE_WITH_K1_INVALID_AS_MISS
        ),
        "worlds_with_all_factual_K4_frames_sham_K2_failure": (
            EXPECTED_ALL_FACTUAL_K4_FRAMES_SHAM_K2_FAILED_WORLD_COUNT
        ),
    }
    if observed != expected:
        raise FairChoiceError(
            "fair-choice construction does not reproduce the frozen sham audit"
        )
    return {"observed": observed, "frozen_expected": expected, "matched": True}


def _assert_frozen_premodel_audits(
    baseline_report: Mapping[str, Any],
    construction_audit: Mapping[str, Any],
) -> None:
    """Abort if rebuilt pre-model facts drift from the sealed protocol."""

    config = _load_frozen_fair_config()
    try:
        configured_sham = dict(
            config["matched_sham"]["expected_pre_model_audit"]
        )
        configured_baseline = dict(
            config["target_blind_structural_baselines"]
            ["expected_pre_model_audit"]
        )
        observed_sham = dict(construction_audit["observed"])
        policies = list(baseline_report["policies"])
        qualifying_counts = list(
            baseline_report["uniform_choice"]
            ["factual_qualifying_action_counts"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise FairChoiceError("pre-model audit payload is malformed") from exc

    configured_sham.pop("interpretation", None)
    if observed_sham != configured_sham:
        raise FairChoiceError("rebuilt sham audit differs from preregistration")

    fixed_semantic = [
        policy
        for policy in policies
        if str(policy.get("policy_id", "")).startswith("fixed-semantic-")
    ]
    if len(fixed_semantic) != RAW_ACTION_COUNT:
        raise FairChoiceError("fixed-semantic baseline family is incomplete")
    best_fixed = min(
        fixed_semantic,
        key=lambda row: (-int(row["factual_F_count"]), str(row["policy_id"])),
    )
    uniform_expectation = Fraction(sum(int(value) for value in qualifying_counts), 10)
    observed_baseline = {
        "uniform_expected_factual_K4_hits": (
            f"{uniform_expectation.numerator}/{uniform_expectation.denominator} = "
            f"{float(uniform_expectation):g}"
        ),
        "uniform_exact_upper_tail_critical_count_at_alpha_0_05": (
            baseline_report["uniform_choice"]["critical_factual_F_count"]
        ),
        "best_fixed_semantic_policy_id": best_fixed["policy_id"],
        "best_fixed_semantic_factual_K4_count": best_fixed["factual_F_count"],
        "B_star_policy_id": baseline_report["B_star_policy_id"],
        "B_star_factual_K4_count": baseline_report["B_star_factual_F_count"],
    }
    if observed_baseline != configured_baseline:
        raise FairChoiceError("rebuilt shortcut baselines differ from preregistration")


def _public_manifest(
    tasks: Sequence[Mapping[str, str]],
    design_commitment_sha256: str,
    current_source_manifest_sha256: str,
    fair_config_sha256: str,
) -> dict[str, Any]:
    if not _is_sha256(design_commitment_sha256):
        raise FairChoiceError("private design commitment is malformed")
    if not _is_sha256(current_source_manifest_sha256):
        raise FairChoiceError("current source manifest digest is malformed")
    if not _is_sha256(fair_config_sha256):
        raise FairChoiceError("fair-choice config digest is malformed")
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": PUBLIC_MANIFEST_KIND,
        "protocol_id": PROTOCOL_ID,
        "task_count": len(tasks),
        "current_source_manifest_sha256": current_source_manifest_sha256,
        "fair_config_file_sha256": fair_config_sha256,
        "private_design_commitment_sha256": design_commitment_sha256,
        "tasks": [dict(task) for task in tasks],
    }
    return {**unsigned, "public_manifest_sha256": _sha256_json(unsigned)}


def build_fair_choice_benchmark(
    sealed_result: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build public/private in-memory artifacts; no model call or file write."""

    assignments, by_index = validate_sealed_scan_result(sealed_result)
    ordered_assignments = tuple(
        sorted(assignments, key=lambda row: int(row["candidate_index"]))
    )
    stratum_ranks: dict[int, int] = {}
    for stratum in spark_lineage.MOTIF_STRATA:
        rows = sorted(
            (
                row
                for row in ordered_assignments
                if row["construction_stratum"] == stratum
            ),
            key=lambda row: int(row["candidate_index"]),
        )
        if len(rows) != 8:
            raise FairChoiceError("each construction stratum must contain eight pairs")
        for rank, row in enumerate(rows):
            stratum_ranks[int(row["candidate_index"])] = rank

    task_records: dict[str, dict[str, str]] = {}
    private_pairs: list[dict[str, Any]] = []
    task_ids: set[str] = set()

    for pair_ordinal, assignment in enumerate(ordered_assignments):
        candidate_index = int(assignment["candidate_index"])
        world_record = by_index[candidate_index]
        public_identity = world_record["public_identity"]
        public_world = public_identity["world"]
        context = _public_context_from_world_entry(public_world)
        witness_slot_id = str(assignment["K4_full_pool_witness_slot_ids"][0])
        pair_anchor = pair_anchor_sha256(
            str(public_identity["public_identity_sha256"]), witness_slot_id
        )
        public_slots = public_identity["slots"]
        factual_slots = [
            slot for slot in public_slots if slot.get("slot_id") == witness_slot_id
        ]
        if len(factual_slots) != 1:
            raise FairChoiceError("witness slot is absent from the public projection")
        factual_slot = factual_slots[0]
        factual_motif_id = str(factual_slot["motif_id"])

        profiles = build_target_free_k1_profiles(
            int(world_record["world_seed"]), context
        )
        factual_profiles = [
            profile for profile in profiles if profile.motif_id == factual_motif_id
        ]
        if len(factual_profiles) != 1:
            raise FairChoiceError("factual motif is absent from target-free K1 catalog")
        factual_profile = factual_profiles[0]
        sham_profile, sham_audit = select_matched_sham(
            factual_motif_id, profiles, pair_anchor
        )
        if factual_profile.motif_sexpr == sham_profile.motif_sexpr:
            raise FairChoiceError("factual and sham prompt sparks must differ")

        action_order = action_order_for_pair(pair_ordinal)
        option_ids = option_ids_for_pair(pair_anchor)
        option_to_raw = {
            option_ids[position]: raw
            for position, raw in enumerate(action_order)
        }
        pair_id = _opaque_id("pair", {"pair_anchor_sha256": pair_anchor})
        arm_task_ids = {
            arm: _opaque_id(
                "task", {"pair_anchor_sha256": pair_anchor, "arm": arm}
            )
            for arm in ARMS
        }
        if set(arm_task_ids.values()).intersection(task_ids):
            raise FairChoiceError("opaque task id collision")
        task_ids.update(arm_task_ids.values())

        prompts = {
            "factual": render_fair_choice_prompt(
                context, factual_profile.motif_sexpr, action_order, option_ids
            ),
            "sham": render_fair_choice_prompt(
                context, sham_profile.motif_sexpr, action_order, option_ids
            ),
        }
        for arm in ARMS:
            task_records[arm_task_ids[arm]] = _task_record(
                arm_task_ids[arm], prompts[arm]
            )

        replayed = _replay_pair_outcomes(
            world_record,
            witness_slot_id,
            factual_motif_id,
            sham_profile.motif_id,
            option_to_raw,
        )
        if not any(
            row["endpoint_flags"]["K4_full_pool"]
            for row in replayed["factual"]
        ):
            raise FairChoiceError("factual arm lost its sealed strong-K4 opportunity")
        if tuple(
            row["endpoint_flags"]["K1"] for row in replayed["factual"]
        ) != factual_profile.k1_mask:
            raise FairChoiceError("factual replay K1 differs from target-free mask")
        if tuple(
            row["endpoint_flags"]["K1"] for row in replayed["sham"]
        ) != sham_profile.k1_mask:
            raise FairChoiceError("sham replay K1 differs from target-free mask")

        private_pairs.append(
            {
                "pair_ordinal": pair_ordinal,
                "pair_id": pair_id,
                "pair_anchor_sha256": pair_anchor,
                "construction_stratum_rank": stratum_ranks[candidate_index],
                "condition_order": (
                    ["factual", "sham"]
                    if stratum_ranks[candidate_index] % 2 == 0
                    else ["sham", "factual"]
                ),
                "world_binding": {
                    "candidate_index": candidate_index,
                    "world_seed": world_record["world_seed"],
                    "public_identity_sha256": public_identity[
                        "public_identity_sha256"
                    ],
                    "construction_stratum": assignment["construction_stratum"],
                    "factual_witness_slot_id": witness_slot_id,
                },
                "prompt_context": context,
                "action_order": list(action_order),
                "option_ids_by_display_position": list(option_ids),
                "option_to_raw_action": dict(option_to_raw),
                "sham_selection_audit": sham_audit,
                "arms": {
                    "factual": {
                        "task_id": arm_task_ids["factual"],
                        "motif": _motif_payload(factual_profile),
                        "actions": replayed["factual"],
                    },
                    "sham": {
                        "task_id": arm_task_ids["sham"],
                        "motif": _motif_payload(sham_profile),
                        "actions": replayed["sham"],
                    },
                },
            }
        )

    # Two public phases avoid condition adjacency while retaining the frozen
    # 4/4 order balance inside every construction stratum.
    public_tasks = [
        task_records[pair["arms"][pair["condition_order"][phase]]["task_id"]]
        for phase in range(2)
        for pair in private_pairs
    ]
    baseline_report = build_baseline_report(private_pairs)
    construction_audit = _construction_audit(private_pairs)
    _assert_frozen_premodel_audits(baseline_report, construction_audit)
    current_source_sha256 = source_manifest(PROJECT_ROOT).get(
        "source_manifest_sha256"
    )
    if not _is_sha256(current_source_sha256):
        raise FairChoiceError("cannot freeze the current fair-choice source manifest")
    fair_config_sha256 = fair_config_file_sha256()
    design_commitment = _sha256_json(
        {
            "protocol_id": PROTOCOL_ID,
            "sealed_scan_sha256": SEALED_SCAN_SHA256,
            "current_source_manifest_sha256": current_source_sha256,
            "fair_config_file_sha256": fair_config_sha256,
            "pairs": private_pairs,
            "baseline_report": baseline_report,
            "construction_audit": construction_audit,
        }
    )
    manifest = _public_manifest(
        public_tasks,
        design_commitment,
        current_source_sha256,
        fair_config_sha256,
    )
    private_unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": PRIVATE_KEY_KIND,
        "protocol_id": PROTOCOL_ID,
        "sealed_scan_sha256": SEALED_SCAN_SHA256,
        "sealed_input_identity": {
            "scan_file_sha256": SEALED_SCAN_FILE_SHA256,
            "scan_sha256": SEALED_SCAN_SHA256,
            "plan_file_sha256": SEALED_SCAN_PLAN_FILE_SHA256,
            "plan_sha256": SEALED_SCAN_PLAN_SHA256,
            "config_file_sha256": SEALED_SCAN_CONFIG_FILE_SHA256,
            "historical_source_manifest_sha256": (
                SEALED_SCAN_SOURCE_MANIFEST_SHA256
            ),
            "artifact_commit": SEALED_SCAN_ARTIFACT_COMMIT,
            "source_freeze_commit": SEALED_SCAN_SOURCE_FREEZE_COMMIT,
            "current_source_manifest_sha256": current_source_sha256,
            "fair_config_file_sha256": fair_config_sha256,
        },
        "private_design_commitment_sha256": design_commitment,
        "public_manifest_sha256": manifest["public_manifest_sha256"],
        "pair_count": len(private_pairs),
        "task_count": len(public_tasks),
        "primary_endpoint": dict(PRIMARY_ENDPOINT_CONTRACT),
        "strong_factual_endpoint": dict(STRONG_FACTUAL_ENDPOINT_CONTRACT),
        "invalid_response_rule": "received_invalid_option_is_miss_no_retry",
        "pairs": private_pairs,
        "baseline_report": baseline_report,
        "construction_audit": construction_audit,
    }
    private_key = {
        **private_unsigned,
        "private_key_sha256": _sha256_json(private_unsigned),
    }
    validate_public_manifest(manifest)
    validate_private_key(private_key, manifest)
    return manifest, private_key


def validate_public_manifest(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        raise FairChoiceError("public manifest must be an object")
    expected = {
        "schema_version",
        "kind",
        "protocol_id",
        "task_count",
        "current_source_manifest_sha256",
        "fair_config_file_sha256",
        "private_design_commitment_sha256",
        "tasks",
        "public_manifest_sha256",
    }
    if set(manifest) != expected:
        raise FairChoiceError("public manifest uses a non-canonical schema")
    unsigned = {
        key: value for key, value in manifest.items() if key != "public_manifest_sha256"
    }
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != PUBLIC_MANIFEST_KIND
        or manifest.get("protocol_id") != PROTOCOL_ID
        or manifest.get("task_count") != TASK_COUNT
        or not _is_sha256(manifest.get("current_source_manifest_sha256"))
        or not _is_sha256(manifest.get("fair_config_file_sha256"))
        or not _is_sha256(manifest.get("private_design_commitment_sha256"))
        or manifest.get("public_manifest_sha256") != _sha256_json(unsigned)
    ):
        raise FairChoiceError("public manifest identity or digest is malformed")
    tasks = manifest.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != TASK_COUNT:
        raise FairChoiceError("public manifest must contain exactly 64 tasks")
    ids: list[str] = []
    for task in tasks:
        if not isinstance(task, Mapping) or set(task) != {
            "task_id",
            "rendered_prompt",
            "prompt_sha256",
        }:
            raise FairChoiceError("public task uses a non-canonical schema")
        task_id = task["task_id"]
        prompt = task["rendered_prompt"]
        if not isinstance(task_id, str) or not task_id.startswith("TASK-"):
            raise FairChoiceError("public task id is not opaque/canonical")
        _validate_rendered_prompt(prompt)
        if task["prompt_sha256"] != _sha256_text(prompt):
            raise FairChoiceError("public task prompt digest mismatch")
        ids.append(task_id)
    if len(set(ids)) != TASK_COUNT:
        raise FairChoiceError("public task ids must be unique")


def _validate_action_rows(actions: Any) -> None:
    if not isinstance(actions, list) or len(actions) != RAW_ACTION_COUNT:
        raise FairChoiceError("private arm must contain ten action outcomes")
    if [row.get("raw_action_index") for row in actions] != list(
        range(RAW_ACTION_COUNT)
    ):
        raise FairChoiceError("private action rows are not the complete raw order")
    for row in actions:
        if not isinstance(row, Mapping) or set(row) != {
            "raw_action_index",
            "option_id",
            "semantic_action",
            "public_features",
            "endpoint_flags",
            "full_pool_counterfactual_bundle_sha256",
        }:
            raise FairChoiceError("private action row schema is malformed")
        raw = int(row["raw_action_index"])
        if row["semantic_action"] != _semantic_action(raw).to_dict():
            raise FairChoiceError("private semantic action identity drifted")
        flags = row["endpoint_flags"]
        if (
            not isinstance(flags, Mapping)
            or set(flags) != set(ENDPOINT_NAMES)
            or any(type(flags[name]) is not bool for name in ENDPOINT_NAMES)
            or (flags["K2"] and not flags["K1"])
            or (flags["K3"] and not flags["K2"])
            or (flags["K4_full_pool"] and not flags["K3"])
            or flags["K2"] != flags["K3"]
        ):
            raise FairChoiceError("private action endpoints are malformed")
        features = row["public_features"]
        if (
            not isinstance(features, Mapping)
            or set(features)
            != {
                "K1_supported",
                "full_domain_positive_count",
                "node_count",
                "child_canonical_hash",
                "child_behavior_hash",
                "parent_behavior_novelty_count",
                "child_behavior_is_constant",
            }
            or features["K1_supported"] != flags["K1"]
        ):
            raise FairChoiceError("private public-action features are malformed")
        if (
            not isinstance(row["option_id"], str)
            or re.fullmatch(r"Q[0-9A-F]{8}", row["option_id"]) is None
        ):
            raise FairChoiceError("private action option id is malformed")
        if flags["K1"]:
            if (
                type(features["full_domain_positive_count"]) is not int
                or not 0 <= features["full_domain_positive_count"] <= 125
                or type(features["node_count"]) is not int
                or features["node_count"] < 1
                or not _is_sha256(features["child_canonical_hash"])
                or not _is_sha256(features["child_behavior_hash"])
                or type(features["parent_behavior_novelty_count"]) is not int
                or not 0 <= features["parent_behavior_novelty_count"] <= 125
                or type(features["child_behavior_is_constant"]) is not bool
                or features["child_behavior_is_constant"]
                != (features["full_domain_positive_count"] in (0, 125))
                or not _is_sha256(
                    row["full_pool_counterfactual_bundle_sha256"]
                )
            ):
                raise FairChoiceError("K1-supported action features are malformed")
        elif any(
            features[name] is not None
            for name in (
                "full_domain_positive_count",
                "node_count",
                "child_canonical_hash",
                "child_behavior_hash",
                "parent_behavior_novelty_count",
                "child_behavior_is_constant",
            )
        ) or row["full_pool_counterfactual_bundle_sha256"] is not None:
            raise FairChoiceError("K1-invalid action leaks nonexistent child features")


def validate_private_key(
    private_key: Mapping[str, Any], public_manifest: Mapping[str, Any]
) -> None:
    validate_public_manifest(public_manifest)
    if not isinstance(private_key, Mapping):
        raise FairChoiceError("private key must be an object")
    unsigned = {
        key: value for key, value in private_key.items() if key != "private_key_sha256"
    }
    expected_private_fields = {
        "schema_version",
        "kind",
        "protocol_id",
        "sealed_scan_sha256",
        "sealed_input_identity",
        "private_design_commitment_sha256",
        "public_manifest_sha256",
        "pair_count",
        "task_count",
        "primary_endpoint",
        "strong_factual_endpoint",
        "invalid_response_rule",
        "pairs",
        "baseline_report",
        "construction_audit",
        "private_key_sha256",
    }
    sealed_identity = {
        "scan_file_sha256": SEALED_SCAN_FILE_SHA256,
        "scan_sha256": SEALED_SCAN_SHA256,
        "plan_file_sha256": SEALED_SCAN_PLAN_FILE_SHA256,
        "plan_sha256": SEALED_SCAN_PLAN_SHA256,
        "config_file_sha256": SEALED_SCAN_CONFIG_FILE_SHA256,
        "historical_source_manifest_sha256": SEALED_SCAN_SOURCE_MANIFEST_SHA256,
        "artifact_commit": SEALED_SCAN_ARTIFACT_COMMIT,
        "source_freeze_commit": SEALED_SCAN_SOURCE_FREEZE_COMMIT,
        "current_source_manifest_sha256": public_manifest.get(
            "current_source_manifest_sha256"
        ),
        "fair_config_file_sha256": public_manifest.get(
            "fair_config_file_sha256"
        ),
    }
    if (
        set(private_key) != expected_private_fields
        or private_key.get("schema_version") != SCHEMA_VERSION
        or private_key.get("kind") != PRIVATE_KEY_KIND
        or private_key.get("protocol_id") != PROTOCOL_ID
        or private_key.get("sealed_scan_sha256") != SEALED_SCAN_SHA256
        or private_key.get("sealed_input_identity") != sealed_identity
        or private_key.get("private_design_commitment_sha256")
        != public_manifest.get("private_design_commitment_sha256")
        or private_key.get("public_manifest_sha256")
        != public_manifest.get("public_manifest_sha256")
        or private_key.get("pair_count") != PAIR_COUNT
        or private_key.get("task_count") != TASK_COUNT
        or private_key.get("primary_endpoint") != PRIMARY_ENDPOINT_CONTRACT
        or private_key.get("strong_factual_endpoint")
        != STRONG_FACTUAL_ENDPOINT_CONTRACT
        or private_key.get("invalid_response_rule")
        != "received_invalid_option_is_miss_no_retry"
        or private_key.get("private_key_sha256") != _sha256_json(unsigned)
    ):
        raise FairChoiceError("private key identity, contract, or digest is malformed")
    tasks = {task["task_id"]: task for task in public_manifest["tasks"]}
    pairs = private_key.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != PAIR_COUNT:
        raise FairChoiceError("private key must contain exactly 32 pairs")
    seen_tasks: set[str] = set()
    position_counts = {
        raw: [0] * RAW_ACTION_COUNT for raw in range(RAW_ACTION_COUNT)
    }
    previous_candidate_index = -1
    stratum_rank_counts = {stratum: Counter() for stratum in spark_lineage.MOTIF_STRATA}
    for ordinal, pair in enumerate(pairs):
        if not isinstance(pair, Mapping) or pair.get("pair_ordinal") != ordinal:
            raise FairChoiceError("private pair ordering is malformed")
        action_order = pair.get("action_order")
        if action_order != list(action_order_for_pair(ordinal)):
            raise FairChoiceError("private pair action counterbalance drifted")
        binding = pair.get("world_binding")
        if not isinstance(binding, Mapping):
            raise FairChoiceError("private world binding is malformed")
        candidate_index = binding.get("candidate_index")
        if type(candidate_index) is not int or candidate_index <= previous_candidate_index:
            raise FairChoiceError("pair ordinal is not global candidate-index order")
        previous_candidate_index = candidate_index
        expected_anchor = pair_anchor_sha256(
            str(binding.get("public_identity_sha256")),
            str(binding.get("factual_witness_slot_id")),
        )
        if pair.get("pair_anchor_sha256") != expected_anchor:
            raise FairChoiceError("private pair anchor is malformed")
        if pair.get("pair_id") != _opaque_id(
            "pair", {"pair_anchor_sha256": expected_anchor}
        ):
            raise FairChoiceError("private opaque pair id drifted")
        option_ids = option_ids_for_pair(expected_anchor)
        if pair.get("option_ids_by_display_position") != list(option_ids):
            raise FairChoiceError("private opaque option IDs drifted")
        option_map = pair.get("option_to_raw_action")
        expected_map = {
            option_ids[position]: raw for position, raw in enumerate(action_order)
        }
        if option_map != expected_map:
            raise FairChoiceError("private option/action mapping is malformed")
        for position, raw in enumerate(action_order):
            position_counts[int(raw)][position] += 1
        context = pair.get("prompt_context")
        arms = pair.get("arms")
        if (
            not isinstance(context, Mapping)
            or not isinstance(arms, Mapping)
            or set(arms) != set(ARMS)
        ):
            raise FairChoiceError("private pair context or arms are malformed")
        stratum = binding.get("construction_stratum")
        rank = pair.get("construction_stratum_rank")
        if (
            stratum not in spark_lineage.MOTIF_STRATA
            or type(rank) is not int
            or not 0 <= rank < 8
        ):
            raise FairChoiceError("private construction-stratum rank is malformed")
        stratum_rank_counts[str(stratum)][rank] += 1
        expected_condition_order = (
            ["factual", "sham"] if rank % 2 == 0 else ["sham", "factual"]
        )
        if pair.get("condition_order") != expected_condition_order:
            raise FairChoiceError("private within-stratum condition order drifted")
        factual_motif = arms["factual"].get("motif")
        sham_motif = arms["sham"].get("motif")
        if (
            not isinstance(factual_motif, Mapping)
            or not isinstance(sham_motif, Mapping)
            or factual_motif.get("motif_sexpr") == sham_motif.get("motif_sexpr")
            or factual_motif.get("stratum") != sham_motif.get("stratum")
            or factual_motif.get("complexity_bucket")
            != sham_motif.get("complexity_bucket")
        ):
            raise FairChoiceError("private factual/sham motif match is malformed")
        for arm in ARMS:
            arm_row = arms[arm]
            task_id = arm_row.get("task_id")
            expected_task_id = _opaque_id(
                "task", {"pair_anchor_sha256": expected_anchor, "arm": arm}
            )
            if (
                task_id != expected_task_id
                or task_id not in tasks
                or task_id in seen_tasks
            ):
                raise FairChoiceError("private arm task binding is malformed")
            seen_tasks.add(task_id)
            _validate_action_rows(arm_row.get("actions"))
            if {
                row["option_id"]: row["raw_action_index"]
                for row in arm_row["actions"]
            } != option_map:
                raise FairChoiceError("arm outcome rows differ from pair option mapping")
            expected_prompt = render_fair_choice_prompt(
                context,
                arm_row["motif"]["motif_sexpr"],
                action_order,
                option_ids,
            )
            if tasks[task_id]["rendered_prompt"] != expected_prompt:
                raise FairChoiceError("public prompt differs from private prompt design")
        if not any(
            row["endpoint_flags"]["K4_full_pool"]
            for row in arms["factual"]["actions"]
        ):
            raise FairChoiceError("private factual arm lacks a strong-K4 opportunity")
        skeleton = render_fair_choice_prompt(
            context, "CONTEXT_PLACEHOLDER", action_order, option_ids
        )
        for arm in ARMS:
            motif = arms[arm]["motif"]["motif_sexpr"]
            observed = tasks[arms[arm]["task_id"]]["rendered_prompt"]
            expected = skeleton.replace("CONTEXT_PLACEHOLDER", motif)
            if observed != expected:
                raise FairChoiceError("paired prompts differ by more than the spark")
    if seen_tasks != set(tasks):
        raise FairChoiceError("public/private task sets differ")
    if any(set(counts) - {3, 4} for counts in position_counts.values()):
        raise FairChoiceError("semantic action positions are not 3/4-counterbalanced")
    if any(
        counts != Counter({rank: 1 for rank in range(8)})
        for counts in stratum_rank_counts.values()
    ):
        raise FairChoiceError("construction-stratum ranks are not exact 0..7")

    expected_public_task_ids = [
        pair["arms"][pair["condition_order"][phase]]["task_id"]
        for phase in range(2)
        for pair in pairs
    ]
    if [task["task_id"] for task in public_manifest["tasks"]] != expected_public_task_ids:
        raise FairChoiceError("public two-phase condition schedule drifted")

    expected_baselines = build_baseline_report(pairs)
    if private_key.get("baseline_report") != expected_baselines:
        raise FairChoiceError("private frozen baseline report drifted")
    expected_audit = _construction_audit(pairs)
    if private_key.get("construction_audit") != expected_audit:
        raise FairChoiceError("private construction audit drifted")
    _assert_frozen_premodel_audits(expected_baselines, expected_audit)
    expected_design_commitment = _sha256_json(
        {
            "protocol_id": PROTOCOL_ID,
            "sealed_scan_sha256": SEALED_SCAN_SHA256,
            "current_source_manifest_sha256": public_manifest[
                "current_source_manifest_sha256"
            ],
            "fair_config_file_sha256": public_manifest[
                "fair_config_file_sha256"
            ],
            "pairs": pairs,
            "baseline_report": expected_baselines,
            "construction_audit": expected_audit,
        }
    )
    if private_key.get("private_design_commitment_sha256") != expected_design_commitment:
        raise FairChoiceError("public/private design commitment drifted")


def _response_expression(value: object) -> object:
    if isinstance(value, Mapping):
        return value.get("expression") if set(value) == {"expression"} else None
    candidate_format = getattr(value, "candidate_format", None)
    if candidate_format == "json_expression":
        return getattr(value, "expression", None)
    return None


def score_model_responses(
    public_manifest: Mapping[str, Any],
    private_key: Mapping[str, Any],
    responses: Mapping[str, object],
    *,
    model_id: str,
) -> dict[str, Any]:
    """Score one complete response set; an invalid received choice is a miss."""

    validate_private_key(private_key, public_manifest)
    if not isinstance(model_id, str) or not model_id:
        raise FairChoiceError("model_id must be non-empty")
    if not isinstance(responses, Mapping):
        raise FairChoiceError("responses must map opaque task ids to received values")
    task_ids = {task["task_id"] for task in public_manifest["tasks"]}
    if set(responses) != task_ids:
        raise FairChoiceError("scoring requires exactly one received response per task")

    pair_rows: list[dict[str, Any]] = []
    invalid_count = 0
    factual_u: list[bool] = []
    sham_u: list[bool] = []
    factual_f: list[bool] = []
    selected_endpoints = {
        arm: {endpoint: [] for endpoint in ENDPOINT_NAMES} for arm in ARMS
    }
    factual_hit_strata: set[str] = set()
    factual_hit_behaviors: set[str] = set()
    factual_nonconstant_hit_count = 0
    for pair in private_key["pairs"]:
        scored_arms: dict[str, dict[str, Any]] = {}
        for arm in ARMS:
            arm_key = pair["arms"][arm]
            task_id = arm_key["task_id"]
            expression = _response_expression(responses[task_id])
            valid = isinstance(expression, str) and expression in pair[
                "option_to_raw_action"
            ]
            selected_raw = (
                int(pair["option_to_raw_action"][expression]) if valid else None
            )
            outcome = _action_by_raw(arm_key["actions"], selected_raw)
            endpoints = {
                name: bool(outcome and outcome["endpoint_flags"][name])
                for name in ENDPOINT_NAMES
            }
            if endpoints["K2"] != endpoints["K3"]:
                raise FairChoiceError("selected K2/K3 endpoints must be identical")
            for endpoint in ENDPOINT_NAMES:
                selected_endpoints[arm][endpoint].append(endpoints[endpoint])
            if not valid:
                invalid_count += 1
            scored_arms[arm] = {
                "task_id": task_id,
                "received_option_valid": valid,
                "selected_raw_action_index": selected_raw,
                "endpoint_flags": endpoints,
                "invalid_is_miss": not valid,
                "retry_performed": False,
            }
            if arm == "factual" and endpoints["K4_full_pool"] and outcome is not None:
                factual_hit_strata.add(
                    str(pair["world_binding"]["construction_stratum"])
                )
                behavior_hash = outcome["public_features"]["child_behavior_hash"]
                if not _is_sha256(behavior_hash):
                    raise FairChoiceError("factual K4 hit lacks child behavior identity")
                factual_hit_behaviors.add(str(behavior_hash))
                factual_nonconstant_hit_count += not bool(
                    outcome["public_features"]["child_behavior_is_constant"]
                )
        factual_u.append(scored_arms["factual"]["endpoint_flags"]["K2"])
        sham_u.append(scored_arms["sham"]["endpoint_flags"]["K2"])
        factual_f.append(
            scored_arms["factual"]["endpoint_flags"]["K4_full_pool"]
        )
        pair_rows.append(
            {
                "pair_id": pair["pair_id"],
                "arms": scored_arms,
                "U_factual": factual_u[-1],
                "U_sham": sham_u[-1],
                "F_factual": factual_f[-1],
            }
        )

    primary = exact_one_sided_mcnemar(factual_u, sham_u)
    baseline = private_key["baseline_report"]
    b_star = tuple(bool(value) for value in baseline["B_star_factual_F_by_pair"])
    versus_b_star = exact_one_sided_mcnemar(factual_f, b_star)
    qualifying_counts = baseline["uniform_choice"][
        "factual_qualifying_action_counts"
    ]
    uniform_tail = poisson_binomial_tail(qualifying_counts, sum(factual_f))
    p_pair = _fraction_from_payload(
        primary["exact_one_sided_p_value"], "paired"
    )
    p_shortcut = _fraction_from_payload(
        versus_b_star["exact_one_sided_p_value"], "shortcut"
    )
    p_route = max(p_pair, p_shortcut)
    critical_count = baseline["uniform_choice"]["critical_factual_F_count"]
    critical_reached = sum(factual_f) >= critical_count
    breadth_payload = {
        "construction_stratum_count": len(factual_hit_strata),
        "construction_strata": sorted(factual_hit_strata),
        "unique_child_behavior_count": len(factual_hit_behaviors),
        "child_behavior_hashes": sorted(factual_hit_behaviors),
        "nonconstant_child_hit_count": factual_nonconstant_hit_count,
        "nonconstant_child_definition": (
            "complete-domain behavior contains both binary outputs"
        ),
    }
    unadjusted_gates = {
        "paired_direction": (
            primary["left_only_count"] > primary["right_only_count"]
        ),
        "shortcut_direction": (
            versus_b_star["left_only_count"]
            > versus_b_star["right_only_count"]
        ),
        "p_route_at_most_alpha_before_holm": p_route <= Fraction(1, 20),
        "uniform_upper_tail_p_at_most_alpha": uniform_tail <= Fraction(1, 20),
        "uniform_critical_count_reached": critical_reached,
        "minimum_two_construction_strata": len(factual_hit_strata) >= 2,
        "minimum_two_child_behavior_hashes": len(factual_hit_behaviors) >= 2,
        "minimum_one_nonconstant_child_behavior": (
            factual_nonconstant_hit_count >= 1
        ),
    }
    selected_endpoint_summary = {
        arm: {
            endpoint: {
                "by_pair": list(selected_endpoints[arm][endpoint]),
                "count": sum(selected_endpoints[arm][endpoint]),
            }
            for endpoint in ENDPOINT_NAMES
        }
        for arm in ARMS
    }
    for arm in ARMS:
        if (
            selected_endpoint_summary[arm]["K2"]["by_pair"]
            != selected_endpoint_summary[arm]["K3"]["by_pair"]
        ):
            raise AssertionError("selected K2/K3 summary drifted")
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": OFFLINE_SCORE_KIND,
        "protocol_id": PROTOCOL_ID,
        "model_id": model_id,
        "public_manifest_sha256": public_manifest["public_manifest_sha256"],
        "private_key_sha256": private_key["private_key_sha256"],
        "current_source_manifest_sha256": public_manifest[
            "current_source_manifest_sha256"
        ],
        "received_response_count": TASK_COUNT,
        "invalid_response_count": invalid_count,
        "invalid_received_response_is_miss": True,
        "retry_performed": False,
        "selected_endpoint_summary": selected_endpoint_summary,
        "route_gate_inputs": {
            "p_pair": _fraction_payload(p_pair),
            "p_shortcut": _fraction_payload(p_shortcut),
            "p_route": _fraction_payload(p_route),
            "p_route_definition": "max(p_pair,p_shortcut)",
            "p_uniform": _fraction_payload(uniform_tail),
            "unadjusted_route_gates": unadjusted_gates,
            "Holm_pending_across_three_frozen_routes": True,
        },
        "pair_primary_U": primary,
        "factual_strong_F_count": sum(factual_f),
        "factual_strong_F_by_pair": factual_f,
        "factual_strong_breadth": breadth_payload,
        "versus_frozen_B_star": {
            "B_star_policy_id": baseline["B_star_policy_id"],
            "B_star_factual_F_count": baseline["B_star_factual_F_count"],
            "paired_test_model_greater_B_star": versus_b_star,
        },
        "versus_uniform_choice": {
            "observed_factual_F_count": sum(factual_f),
            "exact_Poisson_binomial_upper_tail": _fraction_payload(uniform_tail),
            "critical_factual_F_count": critical_count,
            "exceeds_or_meets_critical_value": critical_reached,
        },
        "pairs": pair_rows,
    }
    return {**unsigned, "score_sha256": _sha256_json(unsigned)}


__all__ = [
    "ARMS",
    "BASELINE_POLICY_IDS",
    "CANONICAL_ROUTE_IDS",
    "ENDPOINT_NAMES",
    "FAIR_CONFIG_FILE_SHA256",
    "FairChoiceError",
    "K1SupportProfile",
    "PAIR_COUNT",
    "PROTOCOL_ID",
    "PUBLIC_K1_POLICY_IDS",
    "RAW_ACTION_COUNT",
    "SEALED_SCAN_FILE_SHA256",
    "SEALED_SCAN_PATH",
    "SEALED_SCAN_SHA256",
    "TASK_COUNT",
    "action_order_for_pair",
    "build_baseline_report",
    "build_fair_choice_benchmark",
    "build_target_free_k1_profiles",
    "classify_joint_routes",
    "exact_one_sided_mcnemar",
    "fair_config_file_sha256",
    "holm_adjusted_route_decisions",
    "k1_support_profiles_for_world",
    "load_sealed_scan_result",
    "option_ids_for_pair",
    "pair_anchor_sha256",
    "poisson_binomial_critical_value",
    "poisson_binomial_tail",
    "render_fair_choice_prompt",
    "score_model_responses",
    "select_matched_sham",
    "validate_private_key",
    "validate_public_manifest",
    "validate_sealed_scan_result",
]
