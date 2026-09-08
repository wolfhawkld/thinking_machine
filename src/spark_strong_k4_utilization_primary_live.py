"""Sealed live coordinator for the strict-q6 utilization benchmark.

The module maintains three information layers.  Live planning reads only the
tracked construction plan/public/result artifacts plus private-key metadata
already committed by the construction result.  The target-free paired canary
uses prompts embedded in the sealed live plan and never opens the formal public
manifest or private key.  Primary generation reads only the public manifest;
the private key is first opened by :func:`analyze_primary` after a complete
48-call generation bundle passes public-only validation.

No provider request occurs without an explicit ``--execute`` CLI flag.  A
passing construction result or canary alone never authorizes primary calls;
the runner additionally requires a sealed authorization artifact recording the
human exchangeability decision.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

from . import dsl, spark_closure, spark_lineage
from .credentials import ProviderCredentials, load_provider_credentials
from .provenance import PROJECT_ROOT, protocol_git_pathspecs, source_manifest
from .providers.openai_compatible import (
    HTTPStatusError,
    OpenAICompatibleError,
    OpenAICompatibleGenerator,
    ResponsePayloadError,
    TransportError,
)
from .runner import CANDIDATE_FORMATS, GenerationResponse
from .spark_strong_k4_benchmark import render_fair_choice_prompt
from . import spark_strong_k4_benchmark as prompt_support
from . import spark_strong_k4_utilization_power as utilization_power
from . import spark_strong_k4_utilization_primary_benchmark as benchmark
from . import staged_pilot_v3 as staged_v3
from .staged_pilot_v3 import (
    AcceptedResponseContract,
    V3ResponseContractError,
    route_binding_sha256,
)
from .v3_live import build_v3_generator


SCHEMA_VERSION = 1
PROTOCOL_ID = "spark-strong-k4-utilization-primary-live-v1"
CONFIG_KIND = "spark-strong-k4-utilization-primary-live-config"
PLAN_KIND = "spark-strong-k4-utilization-primary-live-plan"
CANARY_KIND = "spark-strong-k4-utilization-primary-live-canary"
AUTHORIZATION_KIND = "spark-strong-k4-utilization-primary-live-authorization"
GENERATION_KIND = "spark-strong-k4-utilization-primary-generation"
ANALYSIS_KIND = "spark-strong-k4-utilization-primary-analysis"

CONFIG_PATH = (
    PROJECT_ROOT / "configs" / "spark-strong-k4-utilization-primary-live-v1.json"
)
CONFIG_FILE_SHA256 = "b21b2d7a193c674920feaf20e15815f668096f6ccfb9c67c8a16bb5cf1e736f2"
CONFIG_CANONICAL_SHA256 = "dc6b5f686fc9bd6840b38ee45442af890bd8168991610e37d69a675594e9bd85"

LIVE_SOURCE_ADDITIONS = frozenset(
    {
        "configs/spark-strong-k4-utilization-primary-live-v1.json",
        "src/spark_strong_k4_utilization_primary_live.py",
        "tests/test_spark_strong_k4_utilization_primary_live.py",
    }
)

PAIR_COUNT = 24
TASK_COUNT = 48
CANARY_PAIR_COUNT = 4
CANARY_TASK_COUNT = 8
PRIMARY_ALPHA = Fraction(1, 20)
OPAQUE_OPTION_RE = re.compile(r"(?<![A-Z0-9])Q[0-9A-F]{8}(?![A-Z0-9])")


class PrimaryLiveError(RuntimeError):
    """A live artifact, route, response, or information barrier is invalid."""


@dataclass(frozen=True)
class PrimaryPreflight:
    """Validated public-only state returned before the first primary call."""

    plan: dict[str, Any]
    public_manifest: dict[str, Any]
    canary: dict[str, Any]
    authorization: dict[str, Any]
    generator: OpenAICompatibleGenerator
    response_contract: AcceptedResponseContract
    plan_file_sha256: str
    canary_file_sha256: str
    authorization_file_sha256: str


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
        raise PrimaryLiveError("live values must be finite canonical JSON") from exc


def _rendered_json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PrimaryLiveError("live artifact must be finite JSON") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_sha256(value: object, label: str) -> str:
    if not _is_sha256(value):
        raise PrimaryLiveError(f"{label} must be a lowercase SHA-256")
    return str(value)


def _read_json(path: str | Path, label: str) -> tuple[dict[str, Any], bytes]:
    source = Path(path)
    try:
        payload = source.read_bytes()
        value = json.loads(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrimaryLiveError(f"cannot read {label} JSON {source}") from exc
    if not isinstance(value, dict):
        raise PrimaryLiveError(f"{label} JSON must contain one object")
    return value, payload


def _read_bound_json(
    path: str | Path, expected_file_sha256: str, label: str
) -> tuple[dict[str, Any], bytes]:
    value, payload = _read_json(path, label)
    if _sha256_bytes(payload) != _require_sha256(expected_file_sha256, label):
        raise PrimaryLiveError(f"{label} file SHA-256 differs from reviewed value")
    return value, payload


def _write_json_exclusive_0600(value: Mapping[str, Any], output: str | Path) -> str:
    if not isinstance(value, Mapping):
        raise PrimaryLiveError("live JSON output must be an object")
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _rendered_json_bytes(value)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PrimaryLiveError(f"refusing to overwrite live artifact {target}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        raise
    return _sha256_bytes(payload)


def _json_clone(value: object) -> Any:
    return json.loads(_canonical_json_bytes(value).decode("utf-8"))


def validate_config(config: Mapping[str, Any]) -> None:
    if not isinstance(config, Mapping):
        raise PrimaryLiveError("live config must be an object")
    if (
        config.get("schema_version") != SCHEMA_VERSION
        or config.get("kind") != CONFIG_KIND
        or config.get("protocol_id") != PROTOCOL_ID
        or _sha256_json(config) != CONFIG_CANONICAL_SHA256
    ):
        raise PrimaryLiveError("live config identity or canonical seal drifted")
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "benchmark_binding",
        "evidence_scope",
        "primary_route",
        "request_contract",
        "target_free_paired_canary",
        "joint_exchangeability",
        "response_and_failure_policy",
        "exploratory_routes",
        "analysis_contract",
        "artifact_contract",
        "authorization_barrier",
        "model_outputs_read",
        "provider_calls_made",
        "primary_calls_authorized",
    }
    binding = config.get("benchmark_binding")
    evidence = config.get("evidence_scope")
    route = config.get("primary_route")
    request = config.get("request_contract")
    canary = config.get("target_free_paired_canary")
    exchangeability = config.get("joint_exchangeability")
    exploratory = config.get("exploratory_routes")
    analysis = config.get("analysis_contract")
    authorization = config.get("authorization_barrier")
    if set(config) != expected_top or not all(
        isinstance(value, Mapping)
        for value in (
            binding,
            evidence,
            route,
            request,
            canary,
            exchangeability,
            exploratory,
            analysis,
            authorization,
        )
    ):
        raise PrimaryLiveError("live config schema drifted")
    assert isinstance(binding, Mapping)
    assert isinstance(evidence, Mapping)
    assert isinstance(route, Mapping)
    assert isinstance(request, Mapping)
    assert isinstance(canary, Mapping)
    assert isinstance(exchangeability, Mapping)
    assert isinstance(exploratory, Mapping)
    assert isinstance(analysis, Mapping)
    assert isinstance(authorization, Mapping)
    if (
        binding.get("protocol_id") != benchmark.PROTOCOL_ID
        or binding.get("pair_count") != PAIR_COUNT
        or binding.get("task_count") != TASK_COUNT
        or binding.get("benchmark_artifacts_are_immutable") is not True
        or binding.get("live_plan_may_read_private_key") is not False
        or any(
            not _is_sha256(binding.get(field))
            for field in (
                "config_file_sha256",
                "construction_source_manifest_sha256",
                "construction_plan_file_sha256",
                "construction_plan_sha256",
                "public_manifest_file_sha256",
                "public_manifest_sha256",
                "private_key_file_sha256",
                "private_key_sha256",
                "private_design_commitment_sha256",
                "construction_result_file_sha256",
                "construction_result_sha256",
            )
        )
    ):
        raise PrimaryLiveError("live benchmark binding drifted")
    if (
        evidence.get("world_layer") != benchmark.WORLD_LAYER_LABEL
        or evidence.get("model_response_layer")
        != benchmark.MODEL_RESPONSE_LAYER_LABEL
        or evidence.get("independent_heldout_confirmation") is not False
        or evidence.get("construction_is_model_evidence") is not False
        or evidence.get("canary_is_model_evidence") is not False
        or "confirmatory_primary" not in evidence.get("forbidden_claims", [])
    ):
        raise PrimaryLiveError("live evidence scope drifted")
    if (
        route.get("route_id") != "deepseek-pro"
        or route.get("role") != benchmark.MODEL_RESPONSE_LAYER_LABEL
        or route.get("provider_profile") != "deepseek-official-openai-compatible"
        or route.get("request_model") != "deepseek-v4-pro"
        or route.get("required_response_model") != "deepseek-v4-pro"
        or route.get("fresh_target_free_canary_required") is not True
        or route.get("fallback_route_forbidden") is not True
        or route.get("formal_task_calls") != TASK_COUNT
    ):
        raise PrimaryLiveError("live primary route drifted")
    if (
        request.get("temperature") != 0.2
        or request.get("max_output_tokens") != 256
        or request.get("thinking") != "disabled"
        or request.get("timeout_seconds") != 120.0
        or request.get("seed_supported") is not False
        or request.get("physical_attempts_per_task") != 1
        or request.get("retry") is not False
        or request.get("resume") is not False
        or request.get("provider_message") != "rendered_prompt_bytes_only"
        or request.get("conversation_state") is not False
    ):
        raise PrimaryLiveError("live request contract drifted")
    if (
        canary.get("pair_count") != CANARY_PAIR_COUNT
        or canary.get("logical_calls") != CANARY_TASK_COUNT
        or canary.get("target_drawn_or_derived") is not False
        or canary.get("K1_K2_K3_K4_evaluated") is not False
        or canary.get("private_key_read") is not False
        or canary.get("canary_does_not_prove_exchangeability") is not True
        or list(canary.get("motif_strata", [])) != list(spark_lineage.MOTIF_STRATA)
    ):
        raise PrimaryLiveError("live target-free canary contract drifted")
    if (
        exchangeability.get("required_for_primary_test") is not True
        or exchangeability.get("hard_balance_alone_is_proof") is not False
        or exchangeability.get("human_approval_required_before_authorization")
        is not True
        or exploratory.get("decision") != "none_in_this_protocol"
        or exploratory.get("selected_routes") != []
        or exploratory.get("may_replace_pool_with_or_rescue_primary") is not False
        or analysis.get("primary_test")
        != "one-sided exact sign test conditional on non-tie worlds"
        or analysis.get("primary_alpha") != {"numerator": 1, "denominator": 20}
        or analysis.get("B_star_or_posthoc_threshold_selection") is not False
        or authorization.get("authorization_artifact_required_before_primary_calls")
        is not True
        or authorization.get("provider_calls_authorized_by_config_alone") is not False
        or config.get("model_outputs_read") is not False
        or config.get("provider_calls_made") != 0
        or config.get("primary_calls_authorized") is not False
    ):
        raise PrimaryLiveError("live statistical or authorization contract drifted")


def load_frozen_config(path: str | Path = CONFIG_PATH) -> dict[str, Any]:
    config, payload = _read_json(path, "live config")
    if _sha256_bytes(payload) != CONFIG_FILE_SHA256:
        raise PrimaryLiveError("live config file SHA-256 differs from frozen seal")
    validate_config(config)
    return config


def _relative_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise PrimaryLiveError(f"{label} path is malformed")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise PrimaryLiveError(f"{label} path must stay under project root")
    return root / path


def _assert_git_ancestor(root: Path, ancestor: object, label: str) -> str:
    if not isinstance(ancestor, str) or re.fullmatch(r"[0-9a-f]{40}", ancestor) is None:
        raise PrimaryLiveError(f"{label} must be a full lowercase Git commit")
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, "HEAD"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise PrimaryLiveError(f"{label} is not an ancestor of current HEAD")
    return ancestor


def _assert_construction_source_extension(root: Path, config: Mapping[str, Any]) -> None:
    binding = config["benchmark_binding"]
    frozen = _assert_git_ancestor(
        root,
        binding["construction_source_freeze_git_head"],
        "construction source-freeze commit",
    )
    artifact_commit = _assert_git_ancestor(
        root,
        binding["construction_artifact_commit"],
        "construction artifact commit",
    )
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", frozen, artifact_commit],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestry.returncode != 0:
        raise PrimaryLiveError(
            "construction source-freeze commit is not an ancestor of the "
            "construction artifact commit"
        )
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            frozen,
            "HEAD",
            "--",
            *protocol_git_pathspecs(),
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    observed: set[str] = set()
    for line in completed.stdout.splitlines():
        status_code, separator, path = line.partition("\t")
        if separator != "\t" or status_code != "A" or path not in LIVE_SOURCE_ADDITIONS:
            raise PrimaryLiveError(
                "construction protocol source changed instead of receiving only the "
                "three frozen live-layer additions"
            )
        observed.add(path)
    if observed != set(LIVE_SOURCE_ADDITIONS):
        raise PrimaryLiveError("live source additions are incomplete or uncommitted")


def _assert_clean_live_source(root: Path) -> tuple[dict[str, Any], str]:
    manifest = source_manifest(root)
    head, _pathspecs = benchmark._assert_clean_source_freeze(root, manifest)
    return manifest, head


def _assert_current_live_source(
    root: Path, plan: Mapping[str, Any], *, require_current_source: bool
) -> None:
    if not require_current_source:
        return
    current = source_manifest(root)
    if current.get("source_manifest_sha256") != plan.get(
        "live_source_manifest_sha256"
    ):
        raise PrimaryLiveError("current source differs from sealed live plan")
    _head, pathspecs = benchmark._assert_clean_source_freeze(root, current)
    benchmark._assert_frozen_commit_matches_source(
        root, str(plan["live_source_freeze_git_head"]), pathspecs
    )


def _validate_safe_construction_result(
    result: Mapping[str, Any],
    public: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    binding = config["benchmark_binding"]
    unsigned = {
        key: value for key, value in result.items() if key != "construction_result_sha256"
    }
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence",
        "world_layer_label",
        "model_response_layer_label",
        "independent_heldout_confirmation",
        "classification",
        "config_file_sha256",
        "plan_sha256",
        "source_manifest_sha256",
        "reviewed_plan_sha256",
        "reviewed_plan_file_sha256",
        "shard_validation",
        "selection",
        "artifacts",
        "final_benchmark_minted",
        "model_outputs_read",
        "provider_calls_made",
        "provider_calls_authorized",
        "construction_result_sha256",
    }
    artifacts = result.get("artifacts")
    selection = result.get("selection")
    if (
        set(result) != expected_top
        or result.get("kind") != benchmark.CONSTRUCTION_RESULT_KIND
        or result.get("protocol_id") != benchmark.PROTOCOL_ID
        or result.get("construction_result_sha256") != _sha256_json(unsigned)
        or result.get("construction_result_sha256")
        != binding["construction_result_sha256"]
        or result.get("evidence") is not False
        or result.get("independent_heldout_confirmation") is not False
        or result.get("final_benchmark_minted") is not True
        or result.get("model_outputs_read") is not False
        or result.get("provider_calls_made") != 0
        or result.get("provider_calls_authorized") is not False
        or result.get("config_file_sha256") != binding["config_file_sha256"]
        or result.get("plan_sha256") != binding["construction_plan_sha256"]
        or result.get("source_manifest_sha256")
        != binding["construction_source_manifest_sha256"]
        or not isinstance(artifacts, Mapping)
        or artifacts.get("public_manifest_file_sha256")
        != binding["public_manifest_file_sha256"]
        or artifacts.get("public_manifest_sha256")
        != binding["public_manifest_sha256"]
        or artifacts.get("private_key_file_sha256")
        != binding["private_key_file_sha256"]
        or artifacts.get("private_key_sha256") != binding["private_key_sha256"]
        or artifacts.get("private_design_commitment_sha256")
        != binding["private_design_commitment_sha256"]
        or not isinstance(selection, Mapping)
        or selection.get("world_count") != PAIR_COUNT
        or selection.get("task_count") != TASK_COUNT
        or selection.get("selected_q") != 6
        or public.get("public_manifest_sha256")
        != binding["public_manifest_sha256"]
        or public.get("private_design_commitment_sha256")
        != binding["private_design_commitment_sha256"]
    ):
        raise PrimaryLiveError("safe construction result binding drifted")


def _load_construction_inputs(
    config: Mapping[str, Any], root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    binding = config["benchmark_binding"]
    construction_config, _ = _read_bound_json(
        _relative_path(root, binding["config_relative_path"], "construction config"),
        binding["config_file_sha256"],
        "construction config",
    )
    construction_plan, _ = _read_bound_json(
        _relative_path(
            root, binding["construction_plan_relative_path"], "construction plan"
        ),
        binding["construction_plan_file_sha256"],
        "construction plan",
    )
    public, _ = _read_bound_json(
        _relative_path(root, binding["public_manifest_relative_path"], "public manifest"),
        binding["public_manifest_file_sha256"],
        "public manifest",
    )
    result, _ = _read_bound_json(
        _relative_path(
            root, binding["construction_result_relative_path"], "construction result"
        ),
        binding["construction_result_file_sha256"],
        "construction result",
    )
    benchmark.validate_config(construction_config)
    benchmark.validate_construction_plan(
        construction_config,
        construction_plan,
        config_file_sha256=binding["config_file_sha256"],
        project_root=root,
        require_current_source=False,
    )
    benchmark.validate_public_manifest(public)
    if (
        construction_plan.get("plan_sha256") != binding["construction_plan_sha256"]
        or public.get("public_manifest_sha256") != binding["public_manifest_sha256"]
    ):
        raise PrimaryLiveError("construction plan or public semantic binding drifted")
    _validate_safe_construction_result(result, public, config)
    return construction_config, construction_plan, public, result


def _check_private_key_metadata(config: Mapping[str, Any], root: Path) -> None:
    path = _relative_path(
        root, config["benchmark_binding"]["private_key_relative_path"], "private key"
    )
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PrimaryLiveError("private key is absent before live-plan sealing") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise PrimaryLiveError("private key must be a regular mode-0600 local file")


def _read_bound_private_key(
    config: Mapping[str, Any],
    root: Path,
    requested_path: str | Path,
) -> tuple[dict[str, Any], bytes]:
    """Open the configured private key once without following a symlink."""

    binding = config["benchmark_binding"]
    bound = _relative_path(root, binding["private_key_relative_path"], "private key")
    requested = Path(requested_path)
    if not requested.is_absolute():
        requested = root / requested
    if Path(os.path.abspath(requested)) != Path(os.path.abspath(bound)):
        raise PrimaryLiveError("private key path differs from the sealed live config")
    try:
        before = bound.lstat()
        descriptor = os.open(
            bound,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise PrimaryLiveError("cannot securely open the private scoring key") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or stat.S_IMODE(opened.st_mode) != 0o600
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise PrimaryLiveError(
                "private key must remain the configured regular mode-0600 file"
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read()
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrimaryLiveError("private scoring key is not valid JSON") from exc
    if not isinstance(value, dict):
        raise PrimaryLiveError("private scoring key JSON must contain one object")
    if _sha256_bytes(payload) != binding["private_key_file_sha256"]:
        raise PrimaryLiveError("private scoring key file SHA-256 drifted")
    return value, payload


def _canary_option_ids(pair_anchor_sha256: str) -> tuple[str, ...]:
    return benchmark._option_ids(pair_anchor_sha256)


def _build_canary_design(config: Mapping[str, Any]) -> dict[str, Any]:
    serialized = _canonical_json_bytes(config["target_free_paired_canary"]).decode(
        "utf-8"
    )
    return _json_clone(_build_canary_design_cached(serialized))


@lru_cache(maxsize=1)
def _build_canary_design_cached(serialized_canary: str) -> dict[str, Any]:
    canary = json.loads(serialized_canary)
    seeds = list(canary["world_seeds"])
    strata = list(canary["motif_strata"])
    pairs: list[dict[str, Any]] = []
    schedule: list[dict[str, Any]] = []
    tasks_by_id: dict[str, dict[str, Any]] = {}
    namespace = str(canary["canary_id"])
    for pair_ordinal, (world_seed, stratum_name) in enumerate(
        zip(seeds, strata, strict=True)
    ):
        _world, context = benchmark._target_free_prompt_context(int(world_seed))
        left, left_selection = spark_closure._select_motif(
            int(world_seed), 1, stratum_name, namespace=namespace
        )
        right, right_selection = spark_closure._select_motif(
            int(world_seed), 2, stratum_name, namespace=namespace
        )
        if left.motif_id == right.motif_id:
            raise PrimaryLiveError("target-free canary motif pair collided")
        context_sha256 = _sha256_json(context)
        pair_anchor = _sha256_json(
            {
                "protocol_id": PROTOCOL_ID,
                "canary_id": canary["canary_id"],
                "pair_ordinal": pair_ordinal,
                "world_seed": world_seed,
                "motif_stratum": stratum_name,
                "target_free_context_sha256": context_sha256,
                "motif_selection_sha256": [left_selection, right_selection],
            }
        )
        action_order = list(prompt_support.action_order_for_pair(pair_ordinal))
        option_ids = list(_canary_option_ids(pair_anchor))
        arm_tasks: dict[str, dict[str, Any]] = {}
        for arm, motif, selection in (
            ("context_a", left, left_selection),
            ("context_b", right, right_selection),
        ):
            prompt = render_fair_choice_prompt(
                context, dsl.to_sexpr(motif.ast), action_order, option_ids
            )
            identity = _sha256_json(
                {
                    "pair_anchor_sha256": pair_anchor,
                    "arm": arm,
                    "motif_selection_sha256": selection,
                    "prompt_sha256": _sha256_text(prompt),
                }
            )
            task = {
                "task_id": f"CANARY-{identity[:16].upper()}",
                "pair_ordinal": pair_ordinal,
                "arm": arm,
                "motif_stratum": stratum_name,
                "motif_id": motif.motif_id,
                "motif_sexpr": dsl.to_sexpr(motif.ast),
                "motif_selection_sha256": selection,
                "rendered_prompt": prompt,
                "prompt_sha256": _sha256_text(prompt),
                "opaque_option_ids": option_ids,
            }
            arm_tasks[arm] = task
            tasks_by_id[task["task_id"]] = task
        order = (
            ["context_a", "context_b"]
            if pair_ordinal % 2 == 0
            else ["context_b", "context_a"]
        )
        pairs.append(
            {
                "pair_ordinal": pair_ordinal,
                "motif_stratum": stratum_name,
                "target_free_world_seed": world_seed,
                "target_free_context_sha256": context_sha256,
                "pair_anchor_sha256": pair_anchor,
                "action_order": action_order,
                "opaque_option_ids": option_ids,
                "condition_order": order,
                "arms": arm_tasks,
            }
        )
        for phase, arm in enumerate(order, start=1):
            task = arm_tasks[arm]
            schedule.append(
                {
                    "call_index": len(schedule),
                    "pair_ordinal": pair_ordinal,
                    "phase": phase,
                    "arm": arm,
                    "task_id": task["task_id"],
                    "prompt_sha256": task["prompt_sha256"],
                }
            )
    if len(tasks_by_id) != CANARY_TASK_COUNT or len(schedule) != CANARY_TASK_COUNT:
        raise PrimaryLiveError("target-free canary task schedule is incomplete")
    phase_counts = {
        str(phase): {
            arm: sum(
                row["phase"] == phase and row["arm"] == arm for row in schedule
            )
            for arm in benchmark.ARMS
        }
        for phase in (1, 2)
    }
    return {
        "canary_id": canary["canary_id"],
        "evidence": False,
        "evidence_scope": canary["evidence_scope"],
        "pair_count": CANARY_PAIR_COUNT,
        "task_count": CANARY_TASK_COUNT,
        "pairs": pairs,
        "schedule": schedule,
        "schedule_sha256": _sha256_json(schedule),
        "prompt_set_sha256": _sha256_json(
            [
                {
                    "task_id": row["task_id"],
                    "prompt_sha256": row["prompt_sha256"],
                }
                for row in schedule
            ]
        ),
        "phase_arm_counts": phase_counts,
        "target_drawn_or_derived": False,
        "K1_K2_K3_K4_evaluated": False,
        "compressor_run": False,
        "formal_benchmark_task_content_used": False,
        "private_key_read": False,
        "provider_calls_made": 0,
    }


def _expected_request_contract(config: Mapping[str, Any]) -> dict[str, Any]:
    request = config["request_contract"]
    route = config["primary_route"]
    return {
        "adapter": request["adapter"],
        "endpoint_sha256": request["endpoint_sha256"],
        "request_model": route["request_model"],
        "seed_supported": request["seed_supported"],
        "timeout_seconds": request["timeout_seconds"],
        "static_request_extensions_sha256": request[
            "static_request_extensions_sha256"
        ],
        "response_format": request["response_format"],
        "transport_profile": request["transport_profile"],
    }


def build_live_plan(
    config: Mapping[str, Any],
    *,
    live_config_file_sha256: str,
    live_source_manifest_sha256: str,
    live_source_freeze_git_head: str,
) -> dict[str, Any]:
    """Build the sealed pre-canary live plan without opening the private key."""

    validate_config(config)
    binding = config["benchmark_binding"]
    canary_design = _build_canary_design(config)
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence": False,
        "evidence_scope": _json_clone(config["evidence_scope"]),
        "live_config": {
            "relative_path": CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "file_sha256": _require_sha256(
                live_config_file_sha256, "live config file"
            ),
            "canonical_sha256": CONFIG_CANONICAL_SHA256,
        },
        "live_source_manifest_sha256": _require_sha256(
            live_source_manifest_sha256, "live source manifest"
        ),
        "live_source_freeze_git_head": live_source_freeze_git_head,
        "construction_binding": {
            key: _json_clone(value)
            for key, value in binding.items()
            if key
            not in {
                "benchmark_artifacts_are_immutable",
                "live_plan_may_read_private_key",
            }
        },
        "primary_route": _json_clone(config["primary_route"]),
        "request_contract": _json_clone(config["request_contract"]),
        "sanitized_request_contract": _expected_request_contract(config),
        "target_free_paired_canary": canary_design,
        "joint_exchangeability": _json_clone(config["joint_exchangeability"]),
        "response_and_failure_policy": _json_clone(
            config["response_and_failure_policy"]
        ),
        "exploratory_routes": _json_clone(config["exploratory_routes"]),
        "analysis_contract": _json_clone(config["analysis_contract"]),
        "artifact_contract": _json_clone(config["artifact_contract"]),
        "authorization_barrier": _json_clone(config["authorization_barrier"]),
        "private_key_metadata_checked": True,
        "private_key_bytes_read": False,
        "formal_benchmark_model_outputs_read": False,
        "provider_calls_made": 0,
        "primary_calls_authorized": False,
    }
    plan = {**unsigned, "live_plan_sha256": _sha256_json(unsigned)}
    validate_live_plan(config, plan, require_current_source=False)
    return plan


def validate_live_plan(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> None:
    validate_config(config)
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence",
        "evidence_scope",
        "live_config",
        "live_source_manifest_sha256",
        "live_source_freeze_git_head",
        "construction_binding",
        "primary_route",
        "request_contract",
        "sanitized_request_contract",
        "target_free_paired_canary",
        "joint_exchangeability",
        "response_and_failure_policy",
        "exploratory_routes",
        "analysis_contract",
        "artifact_contract",
        "authorization_barrier",
        "private_key_metadata_checked",
        "private_key_bytes_read",
        "formal_benchmark_model_outputs_read",
        "provider_calls_made",
        "primary_calls_authorized",
        "live_plan_sha256",
    }
    if not isinstance(plan, Mapping) or set(plan) != expected_top:
        raise PrimaryLiveError("live plan schema drifted")
    unsigned = {key: value for key, value in plan.items() if key != "live_plan_sha256"}
    binding = config["benchmark_binding"]
    expected_binding = {
        key: _json_clone(value)
        for key, value in binding.items()
        if key not in {"benchmark_artifacts_are_immutable", "live_plan_may_read_private_key"}
    }
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("kind") != PLAN_KIND
        or plan.get("protocol_id") != PROTOCOL_ID
        or plan.get("evidence") is not False
        or plan.get("evidence_scope") != config["evidence_scope"]
        or plan.get("construction_binding") != expected_binding
        or plan.get("primary_route") != config["primary_route"]
        or plan.get("request_contract") != config["request_contract"]
        or plan.get("sanitized_request_contract") != _expected_request_contract(config)
        or plan.get("target_free_paired_canary") != _build_canary_design(config)
        or plan.get("joint_exchangeability") != config["joint_exchangeability"]
        or plan.get("response_and_failure_policy")
        != config["response_and_failure_policy"]
        or plan.get("exploratory_routes") != config["exploratory_routes"]
        or plan.get("analysis_contract") != config["analysis_contract"]
        or plan.get("artifact_contract") != config["artifact_contract"]
        or plan.get("authorization_barrier") != config["authorization_barrier"]
        or plan.get("private_key_metadata_checked") is not True
        or plan.get("private_key_bytes_read") is not False
        or plan.get("formal_benchmark_model_outputs_read") is not False
        or plan.get("provider_calls_made") != 0
        or plan.get("primary_calls_authorized") is not False
        or plan.get("live_plan_sha256") != _sha256_json(unsigned)
    ):
        raise PrimaryLiveError("live plan identity or frozen contracts drifted")
    live_config = plan.get("live_config")
    if (
        not isinstance(live_config, Mapping)
        or live_config.get("relative_path")
        != CONFIG_PATH.relative_to(PROJECT_ROOT).as_posix()
        or live_config.get("file_sha256") != CONFIG_FILE_SHA256
        or live_config.get("canonical_sha256") != CONFIG_CANONICAL_SHA256
        or not _is_sha256(plan.get("live_source_manifest_sha256"))
        or re.fullmatch(
            r"[0-9a-f]{40}", str(plan.get("live_source_freeze_git_head"))
        )
        is None
    ):
        raise PrimaryLiveError("live plan source/config binding drifted")
    _assert_current_live_source(
        Path(project_root), plan, require_current_source=require_current_source
    )


def create_live_plan(
    *,
    config_path: str | Path = CONFIG_PATH,
    project_root: str | Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Validate construction lineage and create an in-memory live plan."""

    root = Path(project_root)
    config = load_frozen_config(config_path)
    _assert_construction_source_extension(root, config)
    _construction_config, _construction_plan, _public, _result = (
        _load_construction_inputs(config, root)
    )
    _check_private_key_metadata(config, root)
    manifest, head = _assert_clean_live_source(root)
    return build_live_plan(
        config,
        live_config_file_sha256=CONFIG_FILE_SHA256,
        live_source_manifest_sha256=str(manifest["source_manifest_sha256"]),
        live_source_freeze_git_head=head,
    )


def _accepted_response_contract(value: object) -> AcceptedResponseContract:
    if not isinstance(value, Mapping):
        raise PrimaryLiveError("accepted response contract must be an object")
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
    if set(value) != expected:
        raise PrimaryLiveError("accepted response contract schema drifted")
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
        raise PrimaryLiveError("accepted response contract is malformed") from exc
    if contract.to_dict() != dict(value):
        raise PrimaryLiveError("accepted response contract is not canonical")
    return contract


def _route_binding_from_contracts(
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


def _derive_response_contract(
    plan: Mapping[str, Any],
    generator: OpenAICompatibleGenerator,
    responses: Sequence[GenerationResponse],
) -> AcceptedResponseContract:
    if len(responses) != CANARY_TASK_COUNT:
        raise PrimaryLiveError("target-free canary did not complete eight calls")
    if any(type(response) is not GenerationResponse for response in responses):
        raise PrimaryLiveError("canary returned a non-canonical response")
    route = plan["primary_route"]
    request = plan["request_contract"]
    if any(response.provider_request_count != 1 for response in responses):
        raise PrimaryLiveError("canary made more than one request for a task")
    if any(
        response.provider_model != route["required_response_model"]
        for response in responses
    ):
        raise PrimaryLiveError("canary response model differs from frozen route")
    if any(response.finish_reason not in {"stop", "length"} for response in responses):
        raise PrimaryLiveError("canary finish reason is unsupported")
    if any(response.output_tokens > request["max_output_tokens"] for response in responses):
        raise PrimaryLiveError("canary response exceeded frozen output cap")
    if any(response.seed_supported is not False for response in responses):
        raise PrimaryLiveError("canary seed capability drifted")
    if any(response.reasoning_tokens not in {None, 0} for response in responses):
        raise PrimaryLiveError("canary did not keep reasoning disabled")
    if any(response.candidate_format not in CANDIDATE_FORMATS for response in responses):
        raise PrimaryLiveError("canary candidate format is outside the closed set")

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
        raise PrimaryLiveError("canary prompt-cache telemetry is inconsistent")

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
        raise PrimaryLiveError("canary provider fingerprint is unstable")

    try:
        contract = AcceptedResponseContract(
            provider_models=(str(route["required_response_model"]),),
            finish_reasons=("stop", "length"),
            max_output_tokens=int(request["max_output_tokens"]),
            seed_supported=False,
            require_zero_reasoning_tokens=True,
            prompt_cache_mode=cache_mode,
            provider_fingerprint_mode=fingerprint_mode,
            provider_fingerprint_sha256=fingerprint_sha256,
        )
        for response in responses:
            contract.validate(response)
    except (TypeError, ValueError, V3ResponseContractError) as exc:
        raise PrimaryLiveError(
            "canary responses do not define one accepted response contract"
        ) from exc
    if generator.sanitized_request_contract() != plan["sanitized_request_contract"]:
        raise PrimaryLiveError("runtime request contract changed during canary")
    return contract


def _option_ids_from_prompt(prompt: str) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(OPAQUE_OPTION_RE.findall(prompt)))
    if len(values) != benchmark.RAW_ACTION_COUNT:
        raise PrimaryLiveError("prompt does not list exactly ten opaque options")
    return values


def _response_record(
    *,
    task_id: str,
    prompt_sha256: str,
    opaque_option_ids: Sequence[str],
    response: GenerationResponse,
) -> dict[str, Any]:
    valid = (
        response.candidate_format == "json_expression"
        and isinstance(response.expression, str)
        and response.expression in opaque_option_ids
    )
    if valid:
        selected = str(response.expression)
        invalid_reason = None
        parse_status = "valid_opaque_option"
    elif response.candidate_format != "json_expression":
        selected = None
        invalid_reason = "candidate_format_not_json_expression"
        parse_status = "received_invalid"
    elif not isinstance(response.expression, str):
        selected = None
        invalid_reason = "expression_not_string"
        parse_status = "received_invalid"
    else:
        selected = None
        invalid_reason = "expression_not_listed_opaque_option"
        parse_status = "received_invalid"
    return {
        "task_id": task_id,
        "prompt_sha256": prompt_sha256,
        "received": True,
        "valid_choice": valid,
        "selected_option_id": selected,
        "invalid_reason": invalid_reason,
        "response": {
            "candidate_expression": selected,
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


_RESPONSE_FIELDS = {
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


def _validate_response_record(
    record: Mapping[str, Any],
    *,
    task_id: str,
    prompt_sha256: str,
    opaque_option_ids: Sequence[str],
    contract: AcceptedResponseContract,
) -> None:
    expected = {
        "task_id",
        "prompt_sha256",
        "received",
        "valid_choice",
        "selected_option_id",
        "invalid_reason",
        "response",
    }
    response = record.get("response")
    if (
        not isinstance(record, Mapping)
        or set(record) != expected
        or record.get("task_id") != task_id
        or record.get("prompt_sha256") != prompt_sha256
        or record.get("received") is not True
        or type(record.get("valid_choice")) is not bool
        or not isinstance(response, Mapping)
        or set(response) != _RESPONSE_FIELDS
    ):
        raise PrimaryLiveError("response record schema or task binding drifted")
    try:
        contract.validate_checkpoint_payload(response)
    except (KeyError, TypeError, ValueError, V3ResponseContractError) as exc:
        raise PrimaryLiveError("response record violates accepted route contract") from exc
    valid = bool(record["valid_choice"])
    selected = record["selected_option_id"]
    candidate_format = response["candidate_format"]
    if valid:
        if (
            candidate_format != "json_expression"
            or selected not in opaque_option_ids
            or response["candidate_expression"] != selected
            or response["candidate_parse_status"] != "valid_opaque_option"
            or record["invalid_reason"] is not None
        ):
            raise PrimaryLiveError("valid opaque response record is malformed")
    else:
        allowed = {
            "candidate_format_not_json_expression",
            "expression_not_string",
            "expression_not_listed_opaque_option",
        }
        if (
            selected is not None
            or response["candidate_expression"] is not None
            or response["candidate_parse_status"] != "received_invalid"
            or record["invalid_reason"] not in allowed
        ):
            raise PrimaryLiveError("received-invalid response record is malformed")
        expected_reason = (
            "candidate_format_not_json_expression"
            if candidate_format != "json_expression"
            else record["invalid_reason"]
        )
        if expected_reason != record["invalid_reason"]:
            raise PrimaryLiveError("received-invalid reason drifted")


def _preflight_generator(
    plan: Mapping[str, Any], generator: OpenAICompatibleGenerator
) -> None:
    if type(generator) is not OpenAICompatibleGenerator:
        raise PrimaryLiveError("live execution requires exact OpenAICompatibleGenerator")
    if generator.model != plan["primary_route"]["request_model"]:
        raise PrimaryLiveError("runtime request model differs from sealed route")
    if generator.sanitized_request_contract() != plan["sanitized_request_contract"]:
        raise PrimaryLiveError("runtime sanitized request contract differs from plan")


def run_target_free_canary(
    plan: Mapping[str, Any],
    generator: OpenAICompatibleGenerator,
    *,
    config: Mapping[str, Any] | None = None,
    project_root: str | Path = PROJECT_ROOT,
    execute: bool = False,
) -> dict[str, Any]:
    """Run eight target-free paired calls; no formal task or private key is read."""

    if execute is not True:
        raise PrimaryLiveError("target-free canary requires explicit execute=True")
    frozen = load_frozen_config() if config is None else dict(config)
    validate_live_plan(
        frozen,
        plan,
        project_root=project_root,
        require_current_source=True,
    )
    _preflight_generator(plan, generator)
    design = plan["target_free_paired_canary"]
    task_by_id = {
        task["task_id"]: task
        for pair in design["pairs"]
        for task in pair["arms"].values()
    }
    responses: list[GenerationResponse] = []
    records: list[dict[str, Any]] = []
    for scheduled in design["schedule"]:
        task = task_by_id[scheduled["task_id"]]
        response = generator.generate(
            task["rendered_prompt"],
            temperature=plan["request_contract"]["temperature"],
            max_output_tokens=plan["request_contract"]["max_output_tokens"],
            round_index=0,
            candidate_index=scheduled["call_index"],
        )
        if type(response) is not GenerationResponse:
            raise PrimaryLiveError("canary adapter returned a non-canonical response")
        responses.append(response)
        records.append(
            {
                **dict(scheduled),
                **_response_record(
                    task_id=task["task_id"],
                    prompt_sha256=task["prompt_sha256"],
                    opaque_option_ids=task["opaque_option_ids"],
                    response=response,
                ),
            }
        )
    contract = _derive_response_contract(plan, generator, responses)
    _assert_current_live_source(Path(project_root), plan, require_current_source=True)
    valid_count = sum(bool(record["valid_choice"]) for record in records)
    counts_by_phase_arm = {
        str(phase): {
            arm: {
                "call_count": sum(
                    row["phase"] == phase and row["arm"] == arm for row in records
                ),
                "valid_choice_count": sum(
                    row["phase"] == phase
                    and row["arm"] == arm
                    and row["valid_choice"]
                    for row in records
                ),
            }
            for arm in benchmark.ARMS
        }
        for phase in (1, 2)
    }
    no_obvious_asymmetry = valid_count == CANARY_TASK_COUNT and all(
        cell == {"call_count": 2, "valid_choice_count": 2}
        for phase in counts_by_phase_arm.values()
        for cell in phase.values()
    )
    passed = valid_count == CANARY_TASK_COUNT and no_obvious_asymmetry
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": CANARY_KIND,
        "protocol_id": PROTOCOL_ID,
        "canary_id": design["canary_id"],
        "evidence": False,
        "evidence_scope": design["evidence_scope"],
        "live_plan_sha256": plan["live_plan_sha256"],
        "live_source_manifest_sha256": plan["live_source_manifest_sha256"],
        "prompt_set_sha256": design["prompt_set_sha256"],
        "schedule_sha256": design["schedule_sha256"],
        "route_id": plan["primary_route"]["route_id"],
        "request_model": plan["primary_route"]["request_model"],
        "response_model": plan["primary_route"]["required_response_model"],
        "sanitized_request_contract": _json_clone(
            plan["sanitized_request_contract"]
        ),
        "accepted_response_contract": contract.to_dict(),
        "route_binding_sha256": route_binding_sha256(generator, contract),
        "prior_route_binding_sha256": plan["primary_route"][
            "prior_route_binding_sha256"
        ],
        "call_count": len(records),
        "transport_failure_count": 0,
        "retry_or_resume": False,
        "valid_choice_count": valid_count,
        "invalid_choice_count": len(records) - valid_count,
        "candidate_format_counts": dict(
            sorted(Counter(response.candidate_format for response in responses).items())
        ),
        "exchangeability_diagnostic": {
            "joint_observable_fields_recorded": True,
            "phase_arm_counts": counts_by_phase_arm,
            "request_and_response_contract_stable": True,
            "no_obvious_dispatch_or_validity_asymmetry": no_obvious_asymmetry,
            "scientific_exchangeability_proven": False,
            "human_approval_still_required": True,
        },
        "passed": passed,
        "formal_benchmark_task_content_read": False,
        "private_key_read": False,
        "benchmark_model_outputs_read": False,
        "provider_calls_made": len(records),
        "primary_calls_authorized": False,
        "records": records,
    }
    artifact = {**unsigned, "canary_sha256": _sha256_json(unsigned)}
    validate_canary_artifact(frozen, plan, artifact, require_current_source=False)
    return artifact


def validate_canary_artifact(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    artifact: Mapping[str, Any],
    *,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> None:
    validate_live_plan(
        config,
        plan,
        project_root=project_root,
        require_current_source=require_current_source,
    )
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "canary_id",
        "evidence",
        "evidence_scope",
        "live_plan_sha256",
        "live_source_manifest_sha256",
        "prompt_set_sha256",
        "schedule_sha256",
        "route_id",
        "request_model",
        "response_model",
        "sanitized_request_contract",
        "accepted_response_contract",
        "route_binding_sha256",
        "prior_route_binding_sha256",
        "call_count",
        "transport_failure_count",
        "retry_or_resume",
        "valid_choice_count",
        "invalid_choice_count",
        "candidate_format_counts",
        "exchangeability_diagnostic",
        "passed",
        "formal_benchmark_task_content_read",
        "private_key_read",
        "benchmark_model_outputs_read",
        "provider_calls_made",
        "primary_calls_authorized",
        "records",
        "canary_sha256",
    }
    if not isinstance(artifact, Mapping) or set(artifact) != expected_top:
        raise PrimaryLiveError("canary artifact schema drifted")
    unsigned = {key: value for key, value in artifact.items() if key != "canary_sha256"}
    design = plan["target_free_paired_canary"]
    if (
        artifact.get("schema_version") != SCHEMA_VERSION
        or artifact.get("kind") != CANARY_KIND
        or artifact.get("protocol_id") != PROTOCOL_ID
        or artifact.get("canary_id") != design["canary_id"]
        or artifact.get("evidence") is not False
        or artifact.get("evidence_scope") != design["evidence_scope"]
        or artifact.get("live_plan_sha256") != plan["live_plan_sha256"]
        or artifact.get("live_source_manifest_sha256")
        != plan["live_source_manifest_sha256"]
        or artifact.get("prompt_set_sha256") != design["prompt_set_sha256"]
        or artifact.get("schedule_sha256") != design["schedule_sha256"]
        or artifact.get("route_id") != plan["primary_route"]["route_id"]
        or artifact.get("request_model")
        != plan["primary_route"]["request_model"]
        or artifact.get("response_model")
        != plan["primary_route"]["required_response_model"]
        or artifact.get("sanitized_request_contract")
        != plan["sanitized_request_contract"]
        or artifact.get("prior_route_binding_sha256")
        != plan["primary_route"]["prior_route_binding_sha256"]
        or artifact.get("call_count") != CANARY_TASK_COUNT
        or artifact.get("transport_failure_count") != 0
        or artifact.get("retry_or_resume") is not False
        or artifact.get("formal_benchmark_task_content_read") is not False
        or artifact.get("private_key_read") is not False
        or artifact.get("benchmark_model_outputs_read") is not False
        or artifact.get("provider_calls_made") != CANARY_TASK_COUNT
        or artifact.get("primary_calls_authorized") is not False
        or artifact.get("canary_sha256") != _sha256_json(unsigned)
    ):
        raise PrimaryLiveError("canary artifact identity or barrier drifted")
    contract = _accepted_response_contract(artifact["accepted_response_contract"])
    if (
        contract.provider_models
        != (str(plan["primary_route"]["required_response_model"]),)
        or contract.finish_reasons != ("stop", "length")
        or contract.max_output_tokens != plan["request_contract"]["max_output_tokens"]
        or contract.seed_supported is not False
        or not contract.require_zero_reasoning_tokens
        or artifact.get("route_binding_sha256")
        != _route_binding_from_contracts(
            plan["sanitized_request_contract"], contract.to_dict()
        )
    ):
        raise PrimaryLiveError("canary accepted response or route binding drifted")
    records = artifact.get("records")
    if not isinstance(records, list) or len(records) != CANARY_TASK_COUNT:
        raise PrimaryLiveError("canary records are incomplete")
    tasks = {
        task["task_id"]: task
        for pair in design["pairs"]
        for task in pair["arms"].values()
    }
    valid_count = 0
    format_counts: Counter[str] = Counter()
    for scheduled, record in zip(design["schedule"], records, strict=True):
        if not isinstance(record, Mapping):
            raise PrimaryLiveError("canary record is malformed")
        for field in ("call_index", "pair_ordinal", "phase", "arm"):
            if record.get(field) != scheduled[field]:
                raise PrimaryLiveError("canary record differs from frozen schedule")
        task = tasks[scheduled["task_id"]]
        response_record = {
            key: record[key]
            for key in (
                "task_id",
                "prompt_sha256",
                "received",
                "valid_choice",
                "selected_option_id",
                "invalid_reason",
                "response",
            )
        }
        _validate_response_record(
            response_record,
            task_id=task["task_id"],
            prompt_sha256=task["prompt_sha256"],
            opaque_option_ids=task["opaque_option_ids"],
            contract=contract,
        )
        valid_count += bool(record["valid_choice"])
        format_counts[str(record["response"]["candidate_format"])] += 1
    diagnostic = artifact.get("exchangeability_diagnostic")
    expected_phase_counts = {
        str(phase): {
            arm: {
                "call_count": sum(
                    row["phase"] == phase and row["arm"] == arm for row in records
                ),
                "valid_choice_count": sum(
                    row["phase"] == phase
                    and row["arm"] == arm
                    and row["valid_choice"]
                    for row in records
                ),
            }
            for arm in benchmark.ARMS
        }
        for phase in (1, 2)
    }
    no_obvious = valid_count == CANARY_TASK_COUNT and all(
        cell == {"call_count": 2, "valid_choice_count": 2}
        for phase in expected_phase_counts.values()
        for cell in phase.values()
    )
    if diagnostic != {
        "joint_observable_fields_recorded": True,
        "phase_arm_counts": expected_phase_counts,
        "request_and_response_contract_stable": True,
        "no_obvious_dispatch_or_validity_asymmetry": no_obvious,
        "scientific_exchangeability_proven": False,
        "human_approval_still_required": True,
    }:
        raise PrimaryLiveError("canary exchangeability diagnostic drifted")
    if (
        artifact.get("valid_choice_count") != valid_count
        or artifact.get("invalid_choice_count") != CANARY_TASK_COUNT - valid_count
        or artifact.get("candidate_format_counts") != dict(sorted(format_counts.items()))
        or artifact.get("passed") is not (valid_count == CANARY_TASK_COUNT and no_obvious)
    ):
        raise PrimaryLiveError("canary pass accounting drifted")


def build_authorization(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    canary: Mapping[str, Any],
    *,
    live_plan_file_sha256: str,
    canary_file_sha256: str,
    human_exchangeability_approved: bool,
) -> dict[str, Any]:
    """Seal all five live gates after explicit human exchangeability approval."""

    validate_live_plan(config, plan, require_current_source=False)
    validate_canary_artifact(
        config, plan, canary, require_current_source=False
    )
    if canary.get("passed") is not True:
        raise PrimaryLiveError("failed target-free canary cannot authorize primary calls")
    if human_exchangeability_approved is not True:
        raise PrimaryLiveError(
            "explicit human exchangeability approval is required for authorization"
        )
    contract = _accepted_response_contract(canary["accepted_response_contract"])
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": AUTHORIZATION_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence": False,
        "evidence_scope": "live_preflight_authorization_only",
        "live_plan_sha256": plan["live_plan_sha256"],
        "live_plan_file_sha256": _require_sha256(
            live_plan_file_sha256, "live plan file"
        ),
        "live_source_manifest_sha256": plan["live_source_manifest_sha256"],
        "canary_sha256": canary["canary_sha256"],
        "canary_file_sha256": _require_sha256(
            canary_file_sha256, "canary file"
        ),
        "route_id": plan["primary_route"]["route_id"],
        "sanitized_request_contract": _json_clone(
            plan["sanitized_request_contract"]
        ),
        "accepted_response_contract": contract.to_dict(),
        "route_binding_sha256": canary["route_binding_sha256"],
        "gate_status": {
            "fresh_target_free_route_canary": "pass",
            "joint_exchangeability_protocol_justification": (
                "human_approved_with_target_free_canary_no_obvious_violation"
            ),
            "response_contract_and_failure_policy": "sealed",
            "exploratory_route_decision": "none_in_this_protocol_sealed",
            "analysis_contract_public_private_hash_binding": "sealed",
        },
        "exchangeability": {
            "human_approval_recorded": True,
            "approval_mechanism": "explicit_authorize_cli_flag_after_review",
            "canary_scientific_exchangeability_proven": False,
            "assumption_remains_required_for_primary_interpretation": True,
            "known_limit": plan["joint_exchangeability"]["known_limit"],
        },
        "exploratory_selected_routes": [],
        "authorized_route_id": "deepseek-pro",
        "authorized_primary_call_count": TASK_COUNT,
        "private_key_read": False,
        "benchmark_model_outputs_read": False,
        "provider_calls_made_during_authorization": 0,
        "primary_calls_authorized": True,
    }
    authorization = {
        **unsigned,
        "authorization_sha256": _sha256_json(unsigned),
    }
    validate_authorization(
        config,
        plan,
        canary,
        authorization,
        require_current_source=False,
    )
    return authorization


def validate_authorization(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    canary: Mapping[str, Any],
    authorization: Mapping[str, Any],
    *,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> None:
    validate_canary_artifact(
        config,
        plan,
        canary,
        project_root=project_root,
        require_current_source=require_current_source,
    )
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence",
        "evidence_scope",
        "live_plan_sha256",
        "live_plan_file_sha256",
        "live_source_manifest_sha256",
        "canary_sha256",
        "canary_file_sha256",
        "route_id",
        "sanitized_request_contract",
        "accepted_response_contract",
        "route_binding_sha256",
        "gate_status",
        "exchangeability",
        "exploratory_selected_routes",
        "authorized_route_id",
        "authorized_primary_call_count",
        "private_key_read",
        "benchmark_model_outputs_read",
        "provider_calls_made_during_authorization",
        "primary_calls_authorized",
        "authorization_sha256",
    }
    if not isinstance(authorization, Mapping) or set(authorization) != expected_top:
        raise PrimaryLiveError("authorization artifact schema drifted")
    unsigned = {
        key: value
        for key, value in authorization.items()
        if key != "authorization_sha256"
    }
    contract = _accepted_response_contract(
        authorization.get("accepted_response_contract")
    )
    expected_gates = {
        "fresh_target_free_route_canary": "pass",
        "joint_exchangeability_protocol_justification": (
            "human_approved_with_target_free_canary_no_obvious_violation"
        ),
        "response_contract_and_failure_policy": "sealed",
        "exploratory_route_decision": "none_in_this_protocol_sealed",
        "analysis_contract_public_private_hash_binding": "sealed",
    }
    expected_exchangeability = {
        "human_approval_recorded": True,
        "approval_mechanism": "explicit_authorize_cli_flag_after_review",
        "canary_scientific_exchangeability_proven": False,
        "assumption_remains_required_for_primary_interpretation": True,
        "known_limit": plan["joint_exchangeability"]["known_limit"],
    }
    if (
        authorization.get("schema_version") != SCHEMA_VERSION
        or authorization.get("kind") != AUTHORIZATION_KIND
        or authorization.get("protocol_id") != PROTOCOL_ID
        or canary.get("passed") is not True
        or authorization.get("evidence") is not False
        or authorization.get("evidence_scope") != "live_preflight_authorization_only"
        or authorization.get("live_plan_sha256") != plan["live_plan_sha256"]
        or not _is_sha256(authorization.get("live_plan_file_sha256"))
        or authorization.get("live_source_manifest_sha256")
        != plan["live_source_manifest_sha256"]
        or authorization.get("canary_sha256") != canary["canary_sha256"]
        or not _is_sha256(authorization.get("canary_file_sha256"))
        or authorization.get("route_id") != "deepseek-pro"
        or authorization.get("sanitized_request_contract")
        != plan["sanitized_request_contract"]
        or contract.to_dict() != canary["accepted_response_contract"]
        or authorization.get("route_binding_sha256")
        != canary["route_binding_sha256"]
        or authorization.get("gate_status") != expected_gates
        or authorization.get("exchangeability") != expected_exchangeability
        or authorization.get("exploratory_selected_routes") != []
        or authorization.get("authorized_route_id") != "deepseek-pro"
        or authorization.get("authorized_primary_call_count") != TASK_COUNT
        or authorization.get("private_key_read") is not False
        or authorization.get("benchmark_model_outputs_read") is not False
        or authorization.get("provider_calls_made_during_authorization") != 0
        or authorization.get("primary_calls_authorized") is not True
        or authorization.get("authorization_sha256") != _sha256_json(unsigned)
    ):
        raise PrimaryLiveError("authorization identity or five-gate barrier drifted")


def _load_plan_file(
    path: str | Path,
    expected_file_sha256: str,
    config: Mapping[str, Any],
    *,
    project_root: str | Path,
    require_current_source: bool,
) -> tuple[dict[str, Any], bytes]:
    plan, raw = _read_bound_json(path, expected_file_sha256, "live plan")
    validate_live_plan(
        config,
        plan,
        project_root=project_root,
        require_current_source=require_current_source,
    )
    return plan, raw


def _load_canary_file(
    path: str | Path,
    expected_file_sha256: str,
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    project_root: str | Path,
    require_current_source: bool,
) -> tuple[dict[str, Any], bytes]:
    canary, raw = _read_bound_json(path, expected_file_sha256, "canary artifact")
    validate_canary_artifact(
        config,
        plan,
        canary,
        project_root=project_root,
        require_current_source=require_current_source,
    )
    return canary, raw


def _load_authorization_file(
    path: str | Path,
    expected_file_sha256: str,
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    canary: Mapping[str, Any],
    *,
    project_root: str | Path,
    require_current_source: bool,
) -> tuple[dict[str, Any], bytes]:
    authorization, raw = _read_bound_json(
        path, expected_file_sha256, "authorization artifact"
    )
    validate_authorization(
        config,
        plan,
        canary,
        authorization,
        project_root=project_root,
        require_current_source=require_current_source,
    )
    return authorization, raw


def preflight_primary(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    canary_path: str | Path,
    expected_canary_file_sha256: str,
    authorization_path: str | Path,
    expected_authorization_file_sha256: str,
    public_manifest_path: str | Path,
    generator: OpenAICompatibleGenerator,
    config: Mapping[str, Any] | None = None,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> PrimaryPreflight:
    """Validate all public identities and gates before the first primary call."""

    root = Path(project_root)
    frozen = load_frozen_config() if config is None else dict(config)
    plan, _plan_raw = _load_plan_file(
        plan_path,
        expected_plan_file_sha256,
        frozen,
        project_root=root,
        require_current_source=require_current_source,
    )
    canary, _canary_raw = _load_canary_file(
        canary_path,
        expected_canary_file_sha256,
        frozen,
        plan,
        project_root=root,
        require_current_source=require_current_source,
    )
    authorization, _authorization_raw = _load_authorization_file(
        authorization_path,
        expected_authorization_file_sha256,
        frozen,
        plan,
        canary,
        project_root=root,
        require_current_source=require_current_source,
    )
    if (
        authorization["live_plan_file_sha256"] != expected_plan_file_sha256
        or authorization["canary_file_sha256"] != expected_canary_file_sha256
    ):
        raise PrimaryLiveError("authorization file bindings differ from reviewed inputs")
    binding = frozen["benchmark_binding"]
    public, public_raw = _read_bound_json(
        public_manifest_path,
        binding["public_manifest_file_sha256"],
        "public manifest",
    )
    benchmark.validate_public_manifest(public)
    if (
        public.get("public_manifest_sha256") != binding["public_manifest_sha256"]
        or public.get("private_design_commitment_sha256")
        != binding["private_design_commitment_sha256"]
        or len(public.get("tasks", [])) != TASK_COUNT
        or _sha256_bytes(public_raw) != binding["public_manifest_file_sha256"]
    ):
        raise PrimaryLiveError("public manifest differs from sealed live plan")
    _construction_config, _construction_plan, canonical_public, _result = (
        _load_construction_inputs(frozen, root)
    )
    if canonical_public != public:
        raise PrimaryLiveError("primary public manifest differs from construction")
    _check_private_key_metadata(frozen, root)
    _preflight_generator(plan, generator)
    contract = _accepted_response_contract(
        authorization["accepted_response_contract"]
    )
    if (
        _route_binding_from_contracts(
            generator.sanitized_request_contract(), contract.to_dict()
        )
        != authorization["route_binding_sha256"]
    ):
        raise PrimaryLiveError("runtime route differs from authorized canary route")
    return PrimaryPreflight(
        plan=dict(plan),
        public_manifest=dict(public),
        canary=dict(canary),
        authorization=dict(authorization),
        generator=generator,
        response_contract=contract,
        plan_file_sha256=expected_plan_file_sha256,
        canary_file_sha256=expected_canary_file_sha256,
        authorization_file_sha256=expected_authorization_file_sha256,
    )


class _AttemptLedger:
    """Exclusive mode-0600 JSONL audit ledger; it is never a scoring input."""

    def __init__(self, path: str | Path, header: Mapping[str, Any]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError as exc:
            raise PrimaryLiveError(
                f"refusing to overwrite primary attempt ledger {self.path}"
            ) from exc
        os.fchmod(descriptor, 0o600)
        self._handle = os.fdopen(descriptor, "wb")
        self.append({"record_type": "header", **dict(header)})

    def append(self, row: Mapping[str, Any]) -> None:
        payload = _canonical_json_bytes(dict(row)) + b"\n"
        self._handle.write(payload)
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)


def _normalized_failure(exc: BaseException) -> dict[str, Any]:
    if isinstance(exc, TransportError):
        return {
            "category": "transport_error",
            "transport_category": exc.category,
            "delivery_ambiguous": exc.delivery_ambiguous,
        }
    if isinstance(exc, HTTPStatusError):
        return {
            "category": "http_status_error",
            "status_code": exc.status_code,
        }
    if isinstance(exc, ResponsePayloadError):
        return {"category": "response_payload_error"}
    if isinstance(exc, OpenAICompatibleError):
        return {"category": "provider_adapter_error"}
    return {"category": "local_runner_error"}


def _postcheck_primary_inputs(
    state: PrimaryPreflight,
    public_manifest_path: str | Path,
    *,
    config: Mapping[str, Any],
    project_root: str | Path,
    require_current_source: bool,
) -> None:
    _assert_current_live_source(
        Path(project_root), state.plan, require_current_source=require_current_source
    )
    observed, _ = _read_bound_json(
        public_manifest_path,
        config["benchmark_binding"]["public_manifest_file_sha256"],
        "public manifest postcheck",
    )
    benchmark.validate_public_manifest(observed)
    if observed != state.public_manifest:
        raise PrimaryLiveError("public manifest changed during primary execution")


def run_primary(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    canary_path: str | Path,
    expected_canary_file_sha256: str,
    authorization_path: str | Path,
    expected_authorization_file_sha256: str,
    public_manifest_path: str | Path,
    attempt_ledger_path: str | Path,
    generator: OpenAICompatibleGenerator,
    config: Mapping[str, Any] | None = None,
    project_root: str | Path = PROJECT_ROOT,
    execute: bool = False,
) -> dict[str, Any]:
    """Run the authorized 48-call schedule and return only a complete bundle."""

    if execute is not True:
        raise PrimaryLiveError("primary generation requires explicit execute=True")
    frozen = load_frozen_config() if config is None else dict(config)
    state = preflight_primary(
        plan_path=plan_path,
        expected_plan_file_sha256=expected_plan_file_sha256,
        canary_path=canary_path,
        expected_canary_file_sha256=expected_canary_file_sha256,
        authorization_path=authorization_path,
        expected_authorization_file_sha256=expected_authorization_file_sha256,
        public_manifest_path=public_manifest_path,
        generator=generator,
        config=frozen,
        project_root=project_root,
        require_current_source=True,
    )
    ledger = _AttemptLedger(
        attempt_ledger_path,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "spark-strong-k4-utilization-primary-attempt-ledger",
            "protocol_id": PROTOCOL_ID,
            "evidence": False,
            "audit_only": True,
            "resumable": False,
            "live_plan_sha256": state.plan["live_plan_sha256"],
            "live_plan_file_sha256": state.plan_file_sha256,
            "canary_sha256": state.canary["canary_sha256"],
            "canary_file_sha256": state.canary_file_sha256,
            "authorization_sha256": state.authorization["authorization_sha256"],
            "authorization_file_sha256": state.authorization_file_sha256,
            "public_manifest_sha256": state.public_manifest[
                "public_manifest_sha256"
            ],
            "public_manifest_file_sha256": frozen["benchmark_binding"][
                "public_manifest_file_sha256"
            ],
            "private_key_read": False,
        },
    )
    records: list[dict[str, Any]] = []
    try:
        for task_ordinal, task in enumerate(state.public_manifest["tasks"]):
            option_ids = _option_ids_from_prompt(task["rendered_prompt"])
            try:
                response = state.generator.generate(
                    task["rendered_prompt"],
                    temperature=state.plan["request_contract"]["temperature"],
                    max_output_tokens=state.plan["request_contract"][
                        "max_output_tokens"
                    ],
                    round_index=0,
                    candidate_index=task_ordinal,
                )
                if type(response) is not GenerationResponse:
                    raise PrimaryLiveError(
                        "primary adapter returned a non-canonical response"
                    )
                state.response_contract.validate(response)
                record = {
                    "call_index": task_ordinal,
                    "task_ordinal": task_ordinal,
                    **_response_record(
                        task_id=task["task_id"],
                        prompt_sha256=task["prompt_sha256"],
                        opaque_option_ids=option_ids,
                        response=response,
                    ),
                }
            except Exception as exc:
                ledger.append(
                    {
                        "record_type": "failure",
                        "call_index": task_ordinal,
                        "task_id": task["task_id"],
                        "prompt_sha256": task["prompt_sha256"],
                        "completed_response_count": len(records),
                        "failure": _normalized_failure(exc),
                        "attempt_classification": frozen["evidence_scope"][
                            "primary_non_evaluable_label"
                        ],
                        "retry_or_resume": False,
                        "private_key_read": False,
                    }
                )
                raise PrimaryLiveError(
                    "primary attempt is non-evaluable under frozen failure policy"
                ) from None
            records.append(record)
            ledger.append({"record_type": "response", **record})
        _postcheck_primary_inputs(
            state,
            public_manifest_path,
            config=frozen,
            project_root=project_root,
            require_current_source=True,
        )
        ledger.append(
            {
                "record_type": "complete",
                "completed_response_count": len(records),
                "retry_or_resume": False,
                "private_key_read": False,
            }
        )
    finally:
        ledger.close()
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": GENERATION_KIND,
        "protocol_id": PROTOCOL_ID,
        "complete": True,
        "evidence": False,
        "unscored_model_responses": True,
        "world_layer_label": benchmark.WORLD_LAYER_LABEL,
        "model_response_layer_label": benchmark.MODEL_RESPONSE_LAYER_LABEL,
        "live_plan_sha256": state.plan["live_plan_sha256"],
        "live_plan_file_sha256": state.plan_file_sha256,
        "canary_sha256": state.canary["canary_sha256"],
        "canary_file_sha256": state.canary_file_sha256,
        "authorization_sha256": state.authorization["authorization_sha256"],
        "authorization_file_sha256": state.authorization_file_sha256,
        "public_manifest_sha256": state.public_manifest["public_manifest_sha256"],
        "public_manifest_file_sha256": frozen["benchmark_binding"][
            "public_manifest_file_sha256"
        ],
        "route_id": "deepseek-pro",
        "response_contract": state.response_contract.to_dict(),
        "route_binding_sha256": state.authorization["route_binding_sha256"],
        "task_count": TASK_COUNT,
        "call_count": len(records),
        "transport_or_missing_failure_count": 0,
        "retry_or_resume": False,
        "private_key_read": False,
        "provider_calls_made": len(records),
        "records": records,
        "records_sha256": _sha256_json(records),
    }
    bundle = {**unsigned, "generation_sha256": _sha256_json(unsigned)}
    validate_generation_bundle(
        frozen,
        state.plan,
        state.canary,
        state.authorization,
        state.public_manifest,
        bundle,
        require_current_source=False,
    )
    return bundle


def validate_generation_bundle(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    canary: Mapping[str, Any],
    authorization: Mapping[str, Any],
    public: Mapping[str, Any],
    bundle: Mapping[str, Any],
    *,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> None:
    validate_authorization(
        config,
        plan,
        canary,
        authorization,
        project_root=project_root,
        require_current_source=require_current_source,
    )
    benchmark.validate_public_manifest(public)
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "complete",
        "evidence",
        "unscored_model_responses",
        "world_layer_label",
        "model_response_layer_label",
        "live_plan_sha256",
        "live_plan_file_sha256",
        "canary_sha256",
        "canary_file_sha256",
        "authorization_sha256",
        "authorization_file_sha256",
        "public_manifest_sha256",
        "public_manifest_file_sha256",
        "route_id",
        "response_contract",
        "route_binding_sha256",
        "task_count",
        "call_count",
        "transport_or_missing_failure_count",
        "retry_or_resume",
        "private_key_read",
        "provider_calls_made",
        "records",
        "records_sha256",
        "generation_sha256",
    }
    if not isinstance(bundle, Mapping) or set(bundle) != expected_top:
        raise PrimaryLiveError("generation bundle schema drifted")
    unsigned = {
        key: value for key, value in bundle.items() if key != "generation_sha256"
    }
    contract = _accepted_response_contract(bundle.get("response_contract"))
    binding = config["benchmark_binding"]
    if (
        bundle.get("schema_version") != SCHEMA_VERSION
        or bundle.get("kind") != GENERATION_KIND
        or bundle.get("protocol_id") != PROTOCOL_ID
        or bundle.get("complete") is not True
        or bundle.get("evidence") is not False
        or bundle.get("unscored_model_responses") is not True
        or bundle.get("world_layer_label") != benchmark.WORLD_LAYER_LABEL
        or bundle.get("model_response_layer_label")
        != benchmark.MODEL_RESPONSE_LAYER_LABEL
        or bundle.get("live_plan_sha256") != plan["live_plan_sha256"]
        or bundle.get("live_plan_file_sha256")
        != authorization["live_plan_file_sha256"]
        or bundle.get("canary_sha256") != canary["canary_sha256"]
        or bundle.get("canary_file_sha256")
        != authorization["canary_file_sha256"]
        or bundle.get("authorization_sha256")
        != authorization["authorization_sha256"]
        or not _is_sha256(bundle.get("authorization_file_sha256"))
        or bundle.get("public_manifest_sha256")
        != public["public_manifest_sha256"]
        or public.get("public_manifest_sha256")
        != binding["public_manifest_sha256"]
        or public.get("source_manifest_sha256")
        != binding["construction_source_manifest_sha256"]
        or public.get("config_file_sha256") != binding["config_file_sha256"]
        or public.get("plan_sha256") != binding["construction_plan_sha256"]
        or public.get("private_design_commitment_sha256")
        != binding["private_design_commitment_sha256"]
        or bundle.get("public_manifest_file_sha256")
        != binding["public_manifest_file_sha256"]
        or bundle.get("route_id") != "deepseek-pro"
        or contract.to_dict() != authorization["accepted_response_contract"]
        or bundle.get("route_binding_sha256")
        != authorization["route_binding_sha256"]
        or bundle.get("task_count") != TASK_COUNT
        or bundle.get("call_count") != TASK_COUNT
        or bundle.get("transport_or_missing_failure_count") != 0
        or bundle.get("retry_or_resume") is not False
        or bundle.get("private_key_read") is not False
        or bundle.get("provider_calls_made") != TASK_COUNT
        or bundle.get("generation_sha256") != _sha256_json(unsigned)
    ):
        raise PrimaryLiveError("generation bundle identity or barrier drifted")
    records = bundle.get("records")
    if (
        not isinstance(records, list)
        or len(records) != TASK_COUNT
        or bundle.get("records_sha256") != _sha256_json(records)
    ):
        raise PrimaryLiveError("generation response records are incomplete")
    for ordinal, (task, record) in enumerate(
        zip(public["tasks"], records, strict=True)
    ):
        if (
            not isinstance(record, Mapping)
            or record.get("call_index") != ordinal
            or record.get("task_ordinal") != ordinal
        ):
            raise PrimaryLiveError("generation record order drifted")
        response_record = {
            key: record[key]
            for key in (
                "task_id",
                "prompt_sha256",
                "received",
                "valid_choice",
                "selected_option_id",
                "invalid_reason",
                "response",
            )
        }
        _validate_response_record(
            response_record,
            task_id=task["task_id"],
            prompt_sha256=task["prompt_sha256"],
            opaque_option_ids=_option_ids_from_prompt(task["rendered_prompt"]),
            contract=contract,
        )


def _score_private_pairs(
    private: Mapping[str, Any], records_by_task: Mapping[str, Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, dict[str, int]]]:
    pair_rows: list[dict[str, Any]] = []
    totals = Counter(
        {
            "favorable": 0,
            "adverse": 0,
            "tie": 0,
            "received_invalid_world": 0,
            "complete_switch": 0,
            "signed_total": 0,
        }
    )
    strata: dict[str, Counter[str]] = {
        stratum: Counter(
            {
                "world_count": 0,
                "favorable": 0,
                "adverse": 0,
                "tie": 0,
                "received_invalid_world": 0,
                "complete_switch": 0,
                "signed_total": 0,
            }
        )
        for stratum in spark_lineage.MOTIF_STRATA
    }
    for pair in private["pairs"]:
        arms = pair["arms"]
        responses = {
            arm: records_by_task[arms[arm]["task_id"]] for arm in benchmark.ARMS
        }
        valid = all(bool(response["valid_choice"]) for response in responses.values())
        own = 0
        cross = 0
        complete_switch = False
        if valid:
            selected = {
                arm: responses[arm]["selected_option_id"] for arm in benchmark.ARMS
            }
            correct = {
                arm: arms[arm]["correct_option_ids"][0] for arm in benchmark.ARMS
            }
            own = sum(selected[arm] == correct[arm] for arm in benchmark.ARMS)
            cross = int(selected["context_a"] == correct["context_b"]) + int(
                selected["context_b"] == correct["context_a"]
            )
            signed = own - cross
            complete_switch = own == 2
            classification = (
                "favorable" if signed > 0 else "adverse" if signed < 0 else "tie"
            )
        else:
            signed = 0
            classification = "tie"
        stratum_name = str(pair["construction_stratum"])
        totals[classification] += 1
        totals["received_invalid_world"] += int(not valid)
        totals["complete_switch"] += int(complete_switch)
        totals["signed_total"] += signed
        strata[stratum_name]["world_count"] += 1
        strata[stratum_name][classification] += 1
        strata[stratum_name]["received_invalid_world"] += int(not valid)
        strata[stratum_name]["complete_switch"] += int(complete_switch)
        strata[stratum_name]["signed_total"] += signed
        pair_rows.append(
            {
                "pair_ordinal": pair["pair_ordinal"],
                "pair_id": pair["pair_id"],
                "construction_stratum": stratum_name,
                "task_ids": {
                    arm: arms[arm]["task_id"] for arm in benchmark.ARMS
                },
                "received_valid_both_arms": valid,
                "received_invalid_world": not valid,
                "own_context_correct_count": own,
                "cross_context_correct_count": cross,
                "world_signed_score": signed,
                "classification": classification,
                "complete_two_arm_context_concordant_switch": complete_switch,
            }
        )
    return (
        pair_rows,
        dict(totals),
        {name: dict(counts) for name, counts in strata.items()},
    )


def analyze_primary(
    *,
    plan_path: str | Path,
    expected_plan_file_sha256: str,
    canary_path: str | Path,
    expected_canary_file_sha256: str,
    authorization_path: str | Path,
    expected_authorization_file_sha256: str,
    public_manifest_path: str | Path,
    generation_path: str | Path,
    expected_generation_file_sha256: str,
    private_key_path: str | Path,
    config: Mapping[str, Any] | None = None,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> dict[str, Any]:
    """Validate a complete public bundle before opening and scoring private key."""

    root = Path(project_root)
    frozen = load_frozen_config() if config is None else dict(config)
    plan, _ = _load_plan_file(
        plan_path,
        expected_plan_file_sha256,
        frozen,
        project_root=root,
        require_current_source=require_current_source,
    )
    canary, _ = _load_canary_file(
        canary_path,
        expected_canary_file_sha256,
        frozen,
        plan,
        project_root=root,
        require_current_source=require_current_source,
    )
    authorization, _ = _load_authorization_file(
        authorization_path,
        expected_authorization_file_sha256,
        frozen,
        plan,
        canary,
        project_root=root,
        require_current_source=require_current_source,
    )
    if (
        authorization["live_plan_file_sha256"] != expected_plan_file_sha256
        or authorization["canary_file_sha256"] != expected_canary_file_sha256
    ):
        raise PrimaryLiveError("authorization reviewed file bindings drifted")
    public, _ = _read_bound_json(
        public_manifest_path,
        frozen["benchmark_binding"]["public_manifest_file_sha256"],
        "public manifest",
    )
    generation, _ = _read_bound_json(
        generation_path,
        expected_generation_file_sha256,
        "complete generation bundle",
    )
    validate_generation_bundle(
        frozen,
        plan,
        canary,
        authorization,
        public,
        generation,
        project_root=root,
        require_current_source=require_current_source,
    )
    if generation["authorization_file_sha256"] != expected_authorization_file_sha256:
        raise PrimaryLiveError("generation authorization file binding drifted")

    # This is deliberately the first private-key byte read in the analysis path.
    private, _private_raw = _read_bound_private_key(
        frozen,
        root,
        private_key_path,
    )
    construction_config, construction_plan, canonical_public, construction_result = (
        _load_construction_inputs(frozen, root)
    )
    if canonical_public != public:
        raise PrimaryLiveError("analysis public manifest differs from construction")
    benchmark.validate_private_key(
        private,
        public,
        config=construction_config,
        plan=construction_plan,
    )
    benchmark.validate_construction_result(construction_result, public, private)
    if (
        private.get("private_key_sha256")
        != frozen["benchmark_binding"]["private_key_sha256"]
        or private.get("private_design_commitment_sha256")
        != frozen["benchmark_binding"]["private_design_commitment_sha256"]
    ):
        raise PrimaryLiveError("private semantic binding differs from live plan")

    records_by_task = {record["task_id"]: record for record in generation["records"]}
    pair_rows, totals, strata = _score_private_pairs(private, records_by_task)
    sign_test = utilization_power.exact_sign_test(
        totals["favorable"], totals["adverse"], PRIMARY_ALPHA
    )
    sign_test["tie_count"] = totals["tie"]
    rejected = bool(sign_test["reject_at_alpha"])
    classification = frozen["evidence_scope"][
        "primary_positive_label" if rejected else "primary_not_detected_label"
    ]
    unsigned = {
        "schema_version": SCHEMA_VERSION,
        "kind": ANALYSIS_KIND,
        "protocol_id": PROTOCOL_ID,
        "evidence": True,
        "world_layer_label": benchmark.WORLD_LAYER_LABEL,
        "model_response_layer_label": benchmark.MODEL_RESPONSE_LAYER_LABEL,
        "independent_heldout_confirmation": False,
        "primary_evaluable": True,
        "primary_classification": classification,
        "claim_scope": (
            "paired net context-responsive unique-action utilization by deepseek-pro "
            "on one fixed development-constructed finite-DSL strict challenge"
        ),
        "forbidden_inferences_remain_forbidden": _json_clone(
            frozen["evidence_scope"]["forbidden_claims"]
        ),
        "live_plan_sha256": plan["live_plan_sha256"],
        "live_plan_file_sha256": expected_plan_file_sha256,
        "canary_sha256": canary["canary_sha256"],
        "canary_file_sha256": expected_canary_file_sha256,
        "authorization_sha256": authorization["authorization_sha256"],
        "authorization_file_sha256": expected_authorization_file_sha256,
        "generation_sha256": generation["generation_sha256"],
        "generation_file_sha256": expected_generation_file_sha256,
        "public_manifest_sha256": public["public_manifest_sha256"],
        "public_manifest_file_sha256": frozen["benchmark_binding"][
            "public_manifest_file_sha256"
        ],
        "private_key_sha256": private["private_key_sha256"],
        "private_key_file_sha256": frozen["benchmark_binding"][
            "private_key_file_sha256"
        ],
        "private_design_commitment_sha256": private[
            "private_design_commitment_sha256"
        ],
        "private_key_loaded_after_complete_generation_validation": True,
        "primary_test": sign_test,
        "world_counts": {
            "total": PAIR_COUNT,
            "favorable": totals["favorable"],
            "adverse": totals["adverse"],
            "tie": totals["tie"],
            "received_invalid_world": totals["received_invalid_world"],
            "complete_two_arm_context_concordant_switch": totals[
                "complete_switch"
            ],
            "signed_total": totals["signed_total"],
        },
        "counts_by_construction_stratum": strata,
        "pair_results": pair_rows,
        "target_blind_structural_baselines": _json_clone(
            private["baseline_report"]
        ),
        "shortcut_baselines_inferential_role": "descriptive_only",
        "B_star_or_posthoc_threshold_selection": False,
        "exchangeability_assumption_human_approved": True,
        "exchangeability_empirically_proven": False,
        "provider_calls_made": generation["provider_calls_made"],
        "model_outputs_read": True,
    }
    analysis = {**unsigned, "analysis_sha256": _sha256_json(unsigned)}
    validate_analysis(
        frozen,
        plan,
        canary,
        authorization,
        public,
        generation,
        private,
        analysis,
        expected_generation_file_sha256=expected_generation_file_sha256,
        require_current_source=False,
    )
    return analysis


def validate_analysis(
    config: Mapping[str, Any],
    plan: Mapping[str, Any],
    canary: Mapping[str, Any],
    authorization: Mapping[str, Any],
    public: Mapping[str, Any],
    generation: Mapping[str, Any],
    private: Mapping[str, Any],
    analysis: Mapping[str, Any],
    *,
    expected_generation_file_sha256: str,
    project_root: str | Path = PROJECT_ROOT,
    require_current_source: bool = True,
) -> None:
    validate_generation_bundle(
        config,
        plan,
        canary,
        authorization,
        public,
        generation,
        project_root=project_root,
        require_current_source=require_current_source,
    )
    root = Path(project_root)
    construction_config, construction_plan, canonical_public, construction_result = (
        _load_construction_inputs(config, root)
    )
    if canonical_public != public:
        raise PrimaryLiveError("analysis public manifest differs from construction")
    benchmark.validate_private_key(
        private,
        public,
        config=construction_config,
        plan=construction_plan,
    )
    benchmark.validate_construction_result(construction_result, public, private)
    records_by_task = {record["task_id"]: record for record in generation["records"]}
    pair_rows, totals, strata = _score_private_pairs(private, records_by_task)
    sign_test = utilization_power.exact_sign_test(
        totals["favorable"], totals["adverse"], PRIMARY_ALPHA
    )
    sign_test["tie_count"] = totals["tie"]
    expected_classification = config["evidence_scope"][
        "primary_positive_label"
        if sign_test["reject_at_alpha"]
        else "primary_not_detected_label"
    ]
    expected_top = {
        "schema_version",
        "kind",
        "protocol_id",
        "evidence",
        "world_layer_label",
        "model_response_layer_label",
        "independent_heldout_confirmation",
        "primary_evaluable",
        "primary_classification",
        "claim_scope",
        "forbidden_inferences_remain_forbidden",
        "live_plan_sha256",
        "live_plan_file_sha256",
        "canary_sha256",
        "canary_file_sha256",
        "authorization_sha256",
        "authorization_file_sha256",
        "generation_sha256",
        "generation_file_sha256",
        "public_manifest_sha256",
        "public_manifest_file_sha256",
        "private_key_sha256",
        "private_key_file_sha256",
        "private_design_commitment_sha256",
        "private_key_loaded_after_complete_generation_validation",
        "primary_test",
        "world_counts",
        "counts_by_construction_stratum",
        "pair_results",
        "target_blind_structural_baselines",
        "shortcut_baselines_inferential_role",
        "B_star_or_posthoc_threshold_selection",
        "exchangeability_assumption_human_approved",
        "exchangeability_empirically_proven",
        "provider_calls_made",
        "model_outputs_read",
        "analysis_sha256",
    }
    if not isinstance(analysis, Mapping) or set(analysis) != expected_top:
        raise PrimaryLiveError("analysis artifact schema drifted")
    unsigned = {
        key: value for key, value in analysis.items() if key != "analysis_sha256"
    }
    expected_counts = {
        "total": PAIR_COUNT,
        "favorable": totals["favorable"],
        "adverse": totals["adverse"],
        "tie": totals["tie"],
        "received_invalid_world": totals["received_invalid_world"],
        "complete_two_arm_context_concordant_switch": totals["complete_switch"],
        "signed_total": totals["signed_total"],
    }
    expected_claim_scope = (
        "paired net context-responsive unique-action utilization by deepseek-pro "
        "on one fixed development-constructed finite-DSL strict challenge"
    )
    binding = config["benchmark_binding"]
    if (
        analysis.get("schema_version") != SCHEMA_VERSION
        or analysis.get("kind") != ANALYSIS_KIND
        or analysis.get("protocol_id") != PROTOCOL_ID
        or analysis.get("evidence") is not True
        or analysis.get("world_layer_label") != benchmark.WORLD_LAYER_LABEL
        or analysis.get("model_response_layer_label")
        != benchmark.MODEL_RESPONSE_LAYER_LABEL
        or analysis.get("independent_heldout_confirmation") is not False
        or analysis.get("primary_evaluable") is not True
        or analysis.get("primary_classification") != expected_classification
        or analysis.get("claim_scope") != expected_claim_scope
        or analysis.get("forbidden_inferences_remain_forbidden")
        != config["evidence_scope"]["forbidden_claims"]
        or analysis.get("live_plan_sha256") != plan["live_plan_sha256"]
        or analysis.get("live_plan_file_sha256")
        != authorization["live_plan_file_sha256"]
        or analysis.get("canary_sha256") != canary["canary_sha256"]
        or analysis.get("canary_file_sha256")
        != authorization["canary_file_sha256"]
        or analysis.get("authorization_sha256")
        != authorization["authorization_sha256"]
        or analysis.get("authorization_file_sha256")
        != generation["authorization_file_sha256"]
        or analysis.get("generation_sha256") != generation["generation_sha256"]
        or analysis.get("generation_file_sha256")
        != _require_sha256(
            expected_generation_file_sha256, "complete generation bundle"
        )
        or analysis.get("public_manifest_sha256")
        != public["public_manifest_sha256"]
        or analysis.get("public_manifest_file_sha256")
        != binding["public_manifest_file_sha256"]
        or analysis.get("private_key_sha256") != private["private_key_sha256"]
        or analysis.get("private_key_sha256") != binding["private_key_sha256"]
        or analysis.get("private_key_file_sha256")
        != binding["private_key_file_sha256"]
        or analysis.get("private_design_commitment_sha256")
        != private["private_design_commitment_sha256"]
        or analysis.get("private_design_commitment_sha256")
        != binding["private_design_commitment_sha256"]
        or analysis.get("private_key_loaded_after_complete_generation_validation")
        is not True
        or analysis.get("primary_test") != sign_test
        or analysis.get("world_counts") != expected_counts
        or analysis.get("counts_by_construction_stratum") != strata
        or analysis.get("pair_results") != pair_rows
        or analysis.get("target_blind_structural_baselines")
        != private["baseline_report"]
        or analysis.get("shortcut_baselines_inferential_role") != "descriptive_only"
        or analysis.get("B_star_or_posthoc_threshold_selection") is not False
        or analysis.get("exchangeability_assumption_human_approved") is not True
        or analysis.get("exchangeability_empirically_proven") is not False
        or analysis.get("provider_calls_made") != TASK_COUNT
        or analysis.get("model_outputs_read") is not True
        or analysis.get("analysis_sha256") != _sha256_json(unsigned)
    ):
        raise PrimaryLiveError("analysis identity, statistics, or claim boundary drifted")


def _ensure_new_output(path: str | Path, label: str) -> Path:
    target = Path(path)
    if os.path.lexists(target):
        raise PrimaryLiveError(f"refusing to overwrite {label} {target}")
    return target


def _load_cli_generator(
    *,
    plan: Mapping[str, Any],
    env_file: str | Path | None,
    env_prefix: str,
) -> OpenAICompatibleGenerator:
    """Load only endpoint/key from the environment; the plan supplies the model."""

    model = str(plan["primary_route"]["request_model"])
    environment = dict(os.environ)
    environment[f"{env_prefix}_MODEL"] = model
    loaded = load_provider_credentials(
        prefix=env_prefix,
        env_file=env_file,
        environ=environment,
    )
    credentials = ProviderCredentials(
        base_url=loaded.base_url,
        model=model,
        api_key=loaded.api_key,
    )
    generator = build_v3_generator(credentials)
    _preflight_generator(plan, generator)
    return generator


def _print_artifact_identity(path: Path, file_sha256: str) -> None:
    print(
        json.dumps(
            {"output": str(path), "file_sha256": file_sha256},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    )


def _add_plan_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-file-sha256", required=True)


def _add_canary_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--canary", type=Path, required=True)
    parser.add_argument("--expected-canary-file-sha256", required=True)


def _add_authorization_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--expected-authorization-file-sha256", required=True)


def _add_deepseek_credential_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--deepseek-env-file", type=Path)
    parser.add_argument("--deepseek-env-prefix", default="DEEPSEEK")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sealed strict-q6 primary-utilization live coordinator"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan", help="seal the credential-free live protocol and canary prompts"
    )
    plan.add_argument("--output", type=Path, required=True)

    canary = commands.add_parser(
        "canary", help="run the eight-call target-free paired route canary"
    )
    _add_plan_binding_arguments(canary)
    _add_deepseek_credential_arguments(canary)
    canary.add_argument("--output", type=Path, required=True)
    canary.add_argument("--execute", action="store_true")

    authorize = commands.add_parser(
        "authorize", help="record the human-reviewed five-gate authorization"
    )
    _add_plan_binding_arguments(authorize)
    _add_canary_binding_arguments(authorize)
    authorize.add_argument("--output", type=Path, required=True)
    authorize.add_argument(
        "--human-approve-exchangeability",
        action="store_true",
        help=(
            "record approval of the required exchangeability assumption after "
            "review; the canary does not prove it"
        ),
    )

    run = commands.add_parser(
        "run", help="run the authorized 48-call primary generation schedule"
    )
    _add_plan_binding_arguments(run)
    _add_canary_binding_arguments(run)
    _add_authorization_binding_arguments(run)
    run.add_argument("--public", type=Path, required=True)
    run.add_argument("--attempt-ledger", type=Path, required=True)
    _add_deepseek_credential_arguments(run)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--execute", action="store_true")

    analyze = commands.add_parser(
        "analyze", help="open the private key only after validating generation"
    )
    _add_plan_binding_arguments(analyze)
    _add_canary_binding_arguments(analyze)
    _add_authorization_binding_arguments(analyze)
    analyze.add_argument("--public", type=Path, required=True)
    analyze.add_argument("--generation", type=Path, required=True)
    analyze.add_argument("--expected-generation-file-sha256", required=True)
    analyze.add_argument("--private", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command == "plan":
        output = _ensure_new_output(args.output, "live plan")
        plan = create_live_plan()
        file_sha256 = _write_json_exclusive_0600(plan, output)
        _print_artifact_identity(output, file_sha256)
        return 0

    if args.command == "canary":
        if not args.execute:
            parser.error("canary requires --execute before any provider request")
        output = _ensure_new_output(args.output, "canary artifact")
        config = load_frozen_config()
        plan, _ = _load_plan_file(
            args.plan,
            args.expected_plan_file_sha256,
            config,
            project_root=PROJECT_ROOT,
            require_current_source=True,
        )
        generator = _load_cli_generator(
            plan=plan,
            env_file=args.deepseek_env_file,
            env_prefix=args.deepseek_env_prefix,
        )
        artifact = run_target_free_canary(
            plan,
            generator,
            config=config,
            execute=True,
        )
        file_sha256 = _write_json_exclusive_0600(artifact, output)
        _print_artifact_identity(output, file_sha256)
        return 0


    if args.command == "authorize":
        output = _ensure_new_output(args.output, "authorization artifact")
        if not args.human_approve_exchangeability:
            parser.error(
                "authorize requires --human-approve-exchangeability after review"
            )
        config = load_frozen_config()
        plan, _ = _load_plan_file(
            args.plan,
            args.expected_plan_file_sha256,
            config,
            project_root=PROJECT_ROOT,
            require_current_source=True,
        )
        canary, _ = _load_canary_file(
            args.canary,
            args.expected_canary_file_sha256,
            config,
            plan,
            project_root=PROJECT_ROOT,
            require_current_source=True,
        )
        authorization = build_authorization(
            config,
            plan,
            canary,
            live_plan_file_sha256=args.expected_plan_file_sha256,
            canary_file_sha256=args.expected_canary_file_sha256,
            human_exchangeability_approved=True,
        )
        file_sha256 = _write_json_exclusive_0600(authorization, output)
        _print_artifact_identity(output, file_sha256)
        return 0


    if args.command == "run":
        if not args.execute:
            parser.error("run requires --execute before any provider request")
        output = _ensure_new_output(args.output, "generation bundle")
        _ensure_new_output(args.attempt_ledger, "primary attempt ledger")
        config = load_frozen_config()
        plan, _ = _load_plan_file(
            args.plan,
            args.expected_plan_file_sha256,
            config,
            project_root=PROJECT_ROOT,
            require_current_source=True,
        )
        generator = _load_cli_generator(
            plan=plan,
            env_file=args.deepseek_env_file,
            env_prefix=args.deepseek_env_prefix,
        )
        bundle = run_primary(
            plan_path=args.plan,
            expected_plan_file_sha256=args.expected_plan_file_sha256,
            canary_path=args.canary,
            expected_canary_file_sha256=args.expected_canary_file_sha256,
            authorization_path=args.authorization,
            expected_authorization_file_sha256=(
                args.expected_authorization_file_sha256
            ),
            public_manifest_path=args.public,
            attempt_ledger_path=args.attempt_ledger,
            generator=generator,
            config=config,
            execute=True,
        )
        file_sha256 = _write_json_exclusive_0600(bundle, output)
        _print_artifact_identity(output, file_sha256)
        return 0


    if args.command == "analyze":
        output = _ensure_new_output(args.output, "analysis artifact")
        analysis = analyze_primary(
            plan_path=args.plan,
            expected_plan_file_sha256=args.expected_plan_file_sha256,
            canary_path=args.canary,
            expected_canary_file_sha256=args.expected_canary_file_sha256,
            authorization_path=args.authorization,
            expected_authorization_file_sha256=(
                args.expected_authorization_file_sha256
            ),
            public_manifest_path=args.public,
            generation_path=args.generation,
            expected_generation_file_sha256=args.expected_generation_file_sha256,
            private_key_path=args.private,
        )
        file_sha256 = _write_json_exclusive_0600(analysis, output)
        _print_artifact_identity(output, file_sha256)
        return 0

    raise AssertionError("unreachable live command")


__all__ = [
    "ANALYSIS_KIND",
    "AUTHORIZATION_KIND",
    "CANARY_KIND",
    "GENERATION_KIND",
    "PLAN_KIND",
    "PROTOCOL_ID",
    "PrimaryLiveError",
    "PrimaryPreflight",
    "analyze_primary",
    "build_authorization",
    "build_live_plan",
    "create_live_plan",
    "main",
    "preflight_primary",
    "run_primary",
    "run_target_free_canary",
    "validate_analysis",
    "validate_authorization",
    "validate_canary_artifact",
    "validate_config",
    "validate_generation_bundle",
    "validate_live_plan",
]


if __name__ == "__main__":
    raise SystemExit(main())
