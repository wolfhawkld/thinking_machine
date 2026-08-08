"""One-request integration canary for the selected development provider.

This command cannot run accidentally: ``--execute`` and an explicit dotenv
path are both required. It uses the first development world and the exact
round-one experiment prompt, but emits only a sanitized non-evidence record.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from .credentials import ProviderCredentials, load_provider_credentials
from .experiment import DEFAULT_CONFIG_PATH, SAMPLING_BASE_SEED, load_config
from .prompts import build_round_prompt
from .providers import OpenAICompatibleGenerator
from .runner import GenerationResponse
from .verifier import Verifier
from .world_generator import generate_world


CANARY_TEMPERATURE = 0.7
CANARY_OUTPUT_TOKENS = 128
CANARY_WORLD = {"seed": 1000, "depth": 3}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_canary(
    credentials: ProviderCredentials,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    generator: Any | None = None,
) -> dict[str, Any]:
    """Make one generation and return a secret-free integration record."""

    config = load_config(config_path)
    configured_name = config["model"].get("name")
    if configured_name != credentials.model:
        raise ValueError(
            "credential model does not match config model; update the development "
            "config deliberately before executing"
        )
    # Seed 1000 is permanently retired for provider integration/calibration;
    # do not consume a comparator-selection pilot world in a future canary.
    world_spec = CANARY_WORLD
    world = generate_world(int(world_spec["seed"]), depth=int(world_spec["depth"]))
    prompt = build_round_prompt(world, round_index=0)
    candidate_generator = generator or OpenAICompatibleGenerator(
        base_url=credentials.base_url,
        api_key=credentials.api_key,
        model=credentials.model,
        seed_supported=False,
        timeout=60.0,
        extra_body={"thinking": {"type": "disabled"}},
    )
    response = candidate_generator.generate(
        prompt,
        temperature=CANARY_TEMPERATURE,
        max_output_tokens=CANARY_OUTPUT_TOKENS,
        seed=SAMPLING_BASE_SEED,
        round_index=0,
        candidate_index=0,
        state={"best_probe_score": 0.0, "round": 0, "improved": False},
    )
    if not isinstance(response, GenerationResponse):
        raise TypeError("canary generator must return GenerationResponse")
    verified = Verifier(world).verify_probe(response.expression)
    return {
        "schema_version": 1,
        "kind": "provider-integration-canary",
        "evidence": False,
        "evidence_reason": "single paid integration request; not an experiment result",
        "provider": {
            **credentials.public_metadata(),
            "credential_present": True,
            "endpoint_mode": "chat-completions",
            "thinking": "disabled",
            "seed_sent": False,
        },
        "request": {
            "temperature": CANARY_TEMPERATURE,
            "max_output_tokens": CANARY_OUTPUT_TOKENS,
            "prompt_sha256": _sha256(prompt),
            "world_seed": int(world_spec["seed"]),
            "world_depth": int(world_spec["depth"]),
            "world_hash": str(world.world_hash),
        },
        "response": {
            "provider_model": response.provider_model,
            "finish_reason": response.finish_reason,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "provider_request_count": response.provider_request_count,
            "seed_supported": response.seed_supported,
            "prompt_cache_hit_tokens": response.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": response.prompt_cache_miss_tokens,
            "reasoning_tokens": response.reasoning_tokens,
            "candidate_expression": str(response.expression),
        },
        "verification": {
            "syntax_valid": verified.syntax_valid,
            "runtime_valid": verified.runtime_valid,
            "probe_accuracy": verified.probe_accuracy,
            "node_count": verified.node_count,
            "depth": verified.depth,
            "failure_codes": list(verified.failure_codes),
        },
    }


def _write_new_json(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--env-prefix", default="DEEPSEEK")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="make exactly one external request (otherwise the command refuses to run)",
    )
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("refusing external API use without --execute")
    credentials = load_provider_credentials(
        prefix=args.env_prefix,
        env_file=args.env_file,
    )
    result = run_canary(credentials, config_path=args.config)
    if args.output is not None:
        _write_new_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
