"""Minimal live launcher for the V3 two-model development experiment.

This module is intentionally a thin research runner.  It provides the missing
operational sequence around the already-frozen experiment core:

1. run one eight-call, generation-only canary per model route;
2. bind the observed response contract and initialize a campaign;
3. execute the strict campaign frontier, stopping after the compatibility gate
   when requested; and
4. invoke the offline private-test finalizer after generation is complete.

Credentials are read at invocation time and never written to an artifact.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .credentials import ProviderCredentials, load_provider_credentials
from .pilot_checkpoint import canonical_json_bytes, sha256_bytes
from .policies import MultiTemperatureExchangePolicy
from .provenance import PROJECT_ROOT, source_manifest
from .providers.openai_compatible import OpenAICompatibleGenerator
from .runner import GenerationResponse, run_episode
from .staged_pilot_v3 import AcceptedResponseContract, route_binding_sha256
from .v3_campaign import (
    acquire_campaign_lock,
    compatibility_screen_path,
    load_campaign_manifest,
    load_compatibility_screen,
    next_shard_frontier,
    publish_campaign_manifest,
    publish_compatibility_screen,
)
from .v3_development import (
    V3_MODEL_STRATA,
    freeze_v3_design,
    load_v3_template,
)
from .v3_finalizer import finalize_v3_campaign
from .v3_generation import run_next_v3_generation_shard
from .verifier import Verifier
from .world_generator import generate_world


V3_CANARY_CALLS = 8
V3_CANARY_WORLD_SEED = 1000
V3_CANARY_WORLD_DEPTH = 3
V3_CANARY_TEMPERATURES = (0.2, 0.7, 0.7, 1.2)
V3_LIVE_TIMEOUT_SECONDS = 120.0
V3_MAX_OUTPUT_TOKENS = 256


class V3LiveError(RuntimeError):
    """A live route or launcher input cannot satisfy the V3 protocol."""


def build_v3_generator(credentials: ProviderCredentials) -> OpenAICompatibleGenerator:
    """Construct the exact seed-free, thinking-disabled V3 provider adapter."""

    if not isinstance(credentials, ProviderCredentials):
        raise TypeError("credentials must be ProviderCredentials")
    return OpenAICompatibleGenerator(
        base_url=credentials.base_url,
        api_key=credentials.api_key,
        model=credentials.model,
        seed_supported=False,
        timeout=V3_LIVE_TIMEOUT_SECONDS,
        extra_body={"thinking": {"type": "disabled"}},
    )


class _CanaryRecorder:
    def __init__(self, generator: OpenAICompatibleGenerator) -> None:
        self.generator = generator
        self.responses: list[GenerationResponse] = []

    def generate(self, prompt: str, **kwargs: Any) -> GenerationResponse:
        response = self.generator.generate(prompt, **kwargs)
        if not isinstance(response, GenerationResponse):
            raise V3LiveError("canary adapter did not return GenerationResponse")
        self.responses.append(response)
        return response


def _response_contract(
    generator: OpenAICompatibleGenerator,
    responses: Sequence[GenerationResponse],
) -> AcceptedResponseContract:
    if len(responses) != V3_CANARY_CALLS:
        raise V3LiveError("route canary did not complete exactly eight calls")
    models = {response.provider_model for response in responses}
    if None in models or len(models) != 1:
        raise V3LiveError("route canary response model was absent or unstable")
    if any(response.finish_reason not in {"stop", "length"} for response in responses):
        raise V3LiveError("route canary returned an unsupported finish reason")
    if any(response.provider_request_count != 1 for response in responses):
        raise V3LiveError("route canary adapter made more than one request per slot")
    if any(response.output_tokens > V3_MAX_OUTPUT_TOKENS for response in responses):
        raise V3LiveError("route canary exceeded the frozen output cap")
    seed_values = {response.seed_supported for response in responses}
    if seed_values != {generator.seed_supported}:
        raise V3LiveError("route canary seed capability was unstable")
    if any(response.reasoning_tokens not in {None, 0} for response in responses):
        raise V3LiveError("route canary did not keep reasoning disabled")

    cache_pairs = [
        (response.prompt_cache_hit_tokens, response.prompt_cache_miss_tokens)
        for response in responses
    ]
    if all(pair == (None, None) for pair in cache_pairs):
        cache_mode = "absent"
    elif all(
        type(hit) is int
        and type(miss) is int
        and response.input_tokens == int(hit) + int(miss)
        for response, (hit, miss) in zip(responses, cache_pairs, strict=True)
    ):
        cache_mode = "complete"
    else:
        raise V3LiveError("route canary prompt-cache telemetry was inconsistent")

    fingerprints = [response.provider_fingerprint for response in responses]
    if all(value is None for value in fingerprints):
        fingerprint_mode = "absent"
        fingerprint_sha256 = None
    elif all(isinstance(value, str) and value.strip() for value in fingerprints) and len(
        set(fingerprints)
    ) == 1:
        fingerprint_mode = "exact_sha256"
        fingerprint_sha256 = hashlib.sha256(
            str(fingerprints[0]).encode("utf-8")
        ).hexdigest()
    else:
        raise V3LiveError("route canary provider fingerprint was inconsistent")

    contract = AcceptedResponseContract(
        provider_models=(str(next(iter(models))),),
        finish_reasons=("stop", "length"),
        max_output_tokens=V3_MAX_OUTPUT_TOKENS,
        seed_supported=generator.seed_supported,
        require_zero_reasoning_tokens=True,
        prompt_cache_mode=cache_mode,
        provider_fingerprint_mode=fingerprint_mode,
        provider_fingerprint_sha256=fingerprint_sha256,
    )
    for response in responses:
        contract.validate(response)
    return contract


def run_route_canary(
    credentials: ProviderCredentials,
    *,
    provider: str,
    stratum_id: str,
    generator: OpenAICompatibleGenerator | None = None,
) -> dict[str, Any]:
    """Run the fixed eight-call route canary without evaluating private test."""

    if stratum_id not in V3_MODEL_STRATA:
        raise V3LiveError("canary stratum is not part of the frozen V3 design")
    if not isinstance(provider, str) or not provider.strip():
        raise V3LiveError("canary provider label must be non-empty")
    live = build_v3_generator(credentials) if generator is None else generator
    if type(live) is not OpenAICompatibleGenerator:
        raise TypeError("canary generator must be OpenAICompatibleGenerator")
    if live.model != credentials.model:
        raise V3LiveError("canary generator model differs from credentials")
    request_contract = live.sanitized_request_contract()
    if request_contract["transport_profile"] != "stdlib-urllib-one-shot-v1":
        raise V3LiveError("live canary requires the standard one-shot transport")

    world = generate_world(V3_CANARY_WORLD_SEED, depth=V3_CANARY_WORLD_DEPTH)
    recorder = _CanaryRecorder(live)
    result = run_episode(
        world,
        recorder,
        verifier=Verifier(counterexample_limit=2),
        policy=MultiTemperatureExchangePolicy(V3_CANARY_TEMPERATURES),
        rounds=2,
        candidates_per_round=4,
        archive_capacity=4,
        max_counterexamples=4,
        max_output_tokens=V3_MAX_OUTPUT_TOKENS,
        max_counterexamples_per_round=2,
        seed=None,
        evaluate_test=False,
    )
    if result.candidate_count != V3_CANARY_CALLS or result.final_test is not None:
        raise V3LiveError("route canary accounting or private-test boundary drifted")
    contract = _response_contract(live, recorder.responses)
    binding = route_binding_sha256(live, contract)
    records = [record for round_records in result.rounds for record in round_records]
    format_counts = Counter(
        str(response.candidate_format) for response in recorder.responses
    )
    search_valid = sum(
        bool(record.syntax_valid and record.runtime_valid) for record in records
    )
    outer_valid = sum(
        response.candidate_format == "json_expression"
        for response in recorder.responses
    )
    return {
        "schema_version": 1,
        "kind": "v3-route-canary",
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence": False,
        "evidence_scope": "route-calibration-only",
        "passed": True,
        "stratum_id": stratum_id,
        "provider": provider.strip(),
        "identity": {
            "request_model": credentials.model,
            "response_model": contract.provider_models[0],
        },
        "sanitized_request_contract": request_contract,
        "accepted_response_contract": contract.to_dict(),
        "route_binding_sha256": binding,
        "protocol": {
            "world_seed": V3_CANARY_WORLD_SEED,
            "world_depth": V3_CANARY_WORLD_DEPTH,
            "logical_calls": V3_CANARY_CALLS,
            "rounds": 2,
            "candidates_per_round": 4,
            "max_output_tokens": V3_MAX_OUTPUT_TOKENS,
            "private_test_evaluated": False,
        },
        "diagnostics": {
            "outer_schema_valid_count": outer_valid,
            "search_valid_count": search_valid,
            "candidate_format_counts": dict(sorted(format_counts.items())),
            "input_tokens": sum(response.input_tokens for response in recorder.responses),
            "output_tokens": sum(response.output_tokens for response in recorder.responses),
            "latency_ms": sum(response.latency_ms for response in recorder.responses),
        },
        "contract_satisfied": True,
    }


def _write_new_json(path: str | Path, value: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value) + b"\n"
    with target.open("xb") as handle:
        handle.write(payload)
        handle.flush()
    target.chmod(0o600)
    return target


def _load_canary(path: str | Path) -> tuple[dict[str, Any], str]:
    source = Path(path)
    try:
        raw = source.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise V3LiveError("cannot read V3 route canary artifact") from exc
    if not isinstance(value, Mapping):
        raise V3LiveError("V3 route canary artifact must be an object")
    return dict(value), sha256_bytes(raw)


def _contract_from_dict(value: Mapping[str, Any]) -> AcceptedResponseContract:
    try:
        return AcceptedResponseContract(
            provider_models=tuple(value["provider_models"]),
            finish_reasons=tuple(value["finish_reasons"]),
            max_output_tokens=int(value["max_output_tokens"]),
            seed_supported=value["seed_supported"],
            require_zero_reasoning_tokens=value["require_zero_reasoning_tokens"],
            prompt_cache_mode=str(value["prompt_cache_mode"]),
            provider_fingerprint_mode=str(value["provider_fingerprint_mode"]),
            provider_fingerprint_sha256=value["provider_fingerprint_sha256"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V3LiveError("canary accepted-response contract is invalid") from exc


def model_binding_from_canary(
    path: str | Path,
    credentials: ProviderCredentials,
    *,
    expected_stratum_id: str,
) -> dict[str, Any]:
    """Convert one completed canary into the exact freeze_v3_design binding."""

    artifact, artifact_sha256 = _load_canary(path)
    if (
        artifact.get("kind") != "v3-route-canary"
        or artifact.get("passed") is not True
        or artifact.get("contract_satisfied") is not True
        or artifact.get("stratum_id") != expected_stratum_id
    ):
        raise V3LiveError("canary does not authorize the requested V3 stratum")
    identity = artifact.get("identity")
    request = artifact.get("sanitized_request_contract")
    response = artifact.get("accepted_response_contract")
    if not all(isinstance(value, Mapping) for value in (identity, request, response)):
        raise V3LiveError("canary route binding fields are missing")
    live = build_v3_generator(credentials)
    if identity["request_model"] != credentials.model:
        raise V3LiveError("canary request model differs from current credentials")
    if dict(request) != live.sanitized_request_contract():
        raise V3LiveError("canary request route differs from the current route")
    contract = _contract_from_dict(response)
    binding = route_binding_sha256(live, contract)
    if artifact.get("route_binding_sha256") != binding:
        raise V3LiveError("canary route binding hash drifted")
    if identity["response_model"] != contract.provider_models[0]:
        raise V3LiveError("canary response identity differs from its contract")
    provider = artifact.get("provider")
    if not isinstance(provider, str) or not provider.strip():
        raise V3LiveError("canary provider label is missing")
    return {
        "provider": provider,
        "name": credentials.model,
        "snapshot": contract.provider_models[0],
        "sanitized_request_contract": dict(request),
        "accepted_response_contract": contract.to_dict(),
        "canary_evidence": {
            "status": "passed",
            "artifact_sha256": artifact_sha256,
            "route_binding_sha256": binding,
            "contract_satisfied": True,
        },
    }


def initialize_v3_campaign(
    campaign_dir: str | Path,
    *,
    deepseek_credentials: ProviderCredentials,
    deepseek_canary: str | Path,
    glm_credentials: ProviderCredentials,
    glm_canary: str | Path,
) -> dict[str, Any]:
    """Freeze the two observed routes and publish the V3 campaign manifest."""

    manifest = source_manifest(PROJECT_ROOT)
    bindings = {
        "official-deepseek-v4": model_binding_from_canary(
            deepseek_canary,
            deepseek_credentials,
            expected_stratum_id="official-deepseek-v4",
        ),
        "official-glm-5.2": model_binding_from_canary(
            glm_canary,
            glm_credentials,
            expected_stratum_id="official-glm-5.2",
        ),
    }
    frozen, plan = freeze_v3_design(
        load_v3_template(),
        model_bindings=bindings,
        source_manifest_sha256=manifest["source_manifest_sha256"],
    )
    envelope = publish_campaign_manifest(
        campaign_dir,
        frozen,
        plan,
        current_source_manifest=manifest,
        forbidden_values=(
            deepseek_credentials.api_key,
            deepseek_credentials.base_url,
            glm_credentials.api_key,
            glm_credentials.base_url,
        ),
    )
    return {
        "status": "campaign_initialized",
        "campaign_dir": str(Path(campaign_dir).resolve()),
        "manifest_payload_sha256": envelope["payload_sha256"],
        "total_shards": len(plan),
        "gate_logical_calls": 160,
        "main_logical_calls": 1920,
    }


def _credential_map(
    deepseek_credentials: ProviderCredentials,
    glm_credentials: ProviderCredentials,
) -> dict[str, ProviderCredentials]:
    return {
        "official-deepseek-v4": deepseek_credentials,
        "official-glm-5.2": glm_credentials,
    }


def run_next_live_shard(
    campaign_dir: str | Path,
    *,
    deepseek_credentials: ProviderCredentials,
    glm_credentials: ProviderCredentials,
) -> dict[str, Any]:
    """Run exactly the current 20-call frontier with its bound model route."""

    envelope = load_campaign_manifest(campaign_dir)
    manifest = envelope["payload"]
    frontier = next_shard_frontier(campaign_dir, envelope)
    credentials_by_stratum = _credential_map(deepseek_credentials, glm_credentials)
    if frontier is None:
        credentials = deepseek_credentials
    else:
        stratum_id = manifest["execution_plan"][frontier]["model_stratum"]
        credentials = credentials_by_stratum[stratum_id]
    generator = build_v3_generator(credentials)
    return run_next_v3_generation_shard(
        campaign_dir,
        generator,
        forbidden_values=(credentials.api_key, credentials.base_url),
    )


def run_live_campaign(
    campaign_dir: str | Path,
    *,
    deepseek_credentials: ProviderCredentials,
    glm_credentials: ProviderCredentials,
    stop_after_gate: bool = True,
    max_shards: int | None = None,
) -> dict[str, Any]:
    """Advance sequentially until gate, generation completion, or max_shards."""

    if max_shards is not None and (type(max_shards) is not int or max_shards < 1):
        raise ValueError("max_shards must be a positive integer or None")
    completed = 0
    while max_shards is None or completed < max_shards:
        envelope = load_campaign_manifest(campaign_dir)
        frontier = next_shard_frontier(campaign_dir, envelope)
        if stop_after_gate and (frontier is None or frontier >= 8):
            if not compatibility_screen_path(campaign_dir).exists():
                if frontier != 8:
                    raise V3LiveError(
                        "gate screen is absent after the campaign advanced beyond gate"
                    )
                # Recover the ordinary crash window after gate seal 8 and
                # before screen publication without starting main shard 8.
                with acquire_campaign_lock(campaign_dir, blocking=False) as lease:
                    publish_compatibility_screen(
                        campaign_dir,
                        envelope,
                        lease=lease,
                    )
            screen = load_compatibility_screen(campaign_dir, envelope)["payload"]
            return {
                "status": (
                    "compatibility_screen_passed"
                    if screen["status"] == "passed"
                    else "compatibility_screen_failed"
                ),
                "sealed_episode_count": 8,
                "next_shard_index": 8 if screen["status"] == "passed" else None,
                "private_test_evaluated": False,
            }
        status = run_next_live_shard(
            campaign_dir,
            deepseek_credentials=deepseek_credentials,
            glm_credentials=glm_credentials,
        )
        completed += 1
        if status["status"] in {
            "compatibility_screen_failed",
            "generation_complete",
        }:
            return status
        if stop_after_gate and status["status"] == "compatibility_screen_passed":
            return status
    return {
        "status": "shard_limit_reached",
        "shards_advanced_this_invocation": completed,
        "private_test_evaluated": False,
    }


def finalize_live_campaign(campaign_dir: str | Path) -> dict[str, Any]:
    """Release the frozen private endpoint only after the full generation barrier."""

    return finalize_v3_campaign(campaign_dir)


def _credentials(path: Path, prefix: str) -> ProviderCredentials:
    return load_provider_credentials(prefix=prefix, env_file=path)


def _add_two_route_credentials(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--deepseek-env-file", type=Path, required=True)
    parser.add_argument("--deepseek-env-prefix", default="DEEPSEEK")
    parser.add_argument("--glm-env-file", type=Path, required=True)
    parser.add_argument("--glm-env-prefix", default="HERMES")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    canary = commands.add_parser("canary", help="run one eight-call route canary")
    canary.add_argument("--stratum", choices=V3_MODEL_STRATA, required=True)
    canary.add_argument("--provider", required=True)
    canary.add_argument("--env-file", type=Path, required=True)
    canary.add_argument("--env-prefix", required=True)
    canary.add_argument("--output", type=Path, required=True)
    canary.add_argument("--execute", action="store_true")

    initialize = commands.add_parser("init", help="freeze routes and initialize campaign")
    initialize.add_argument("--campaign-dir", type=Path, required=True)
    initialize.add_argument("--deepseek-canary", type=Path, required=True)
    initialize.add_argument("--glm-canary", type=Path, required=True)
    _add_two_route_credentials(initialize)

    run = commands.add_parser("run", help="advance the live generation campaign")
    run.add_argument("--campaign-dir", type=Path, required=True)
    run.add_argument(
        "--continue-main",
        action="store_true",
        help="continue beyond the 160-call gate; the default stops for review",
    )
    run.add_argument("--max-shards", type=int)
    _add_two_route_credentials(run)

    finalize = commands.add_parser("finalize", help="run the offline private finalizer")
    finalize.add_argument("--campaign-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "canary":
        if not args.execute:
            raise V3LiveError("refusing paid canary without --execute")
        credentials = _credentials(args.env_file, args.env_prefix)
        result = run_route_canary(
            credentials,
            provider=args.provider,
            stratum_id=args.stratum,
        )
        _write_new_json(args.output, result)
    elif args.command == "init":
        result = initialize_v3_campaign(
            args.campaign_dir,
            deepseek_credentials=_credentials(
                args.deepseek_env_file, args.deepseek_env_prefix
            ),
            deepseek_canary=args.deepseek_canary,
            glm_credentials=_credentials(args.glm_env_file, args.glm_env_prefix),
            glm_canary=args.glm_canary,
        )
    elif args.command == "run":
        result = run_live_campaign(
            args.campaign_dir,
            deepseek_credentials=_credentials(
                args.deepseek_env_file, args.deepseek_env_prefix
            ),
            glm_credentials=_credentials(args.glm_env_file, args.glm_env_prefix),
            stop_after_gate=not args.continue_main,
            max_shards=args.max_shards,
        )
    else:
        result = finalize_live_campaign(args.campaign_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


__all__ = [
    "V3LiveError",
    "build_v3_generator",
    "finalize_live_campaign",
    "initialize_v3_campaign",
    "main",
    "model_binding_from_canary",
    "run_live_campaign",
    "run_next_live_shard",
    "run_route_canary",
]


if __name__ == "__main__":
    raise SystemExit(main())
