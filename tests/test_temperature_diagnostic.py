from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.credentials import ProviderCredentials
from src.runner import GenerationResponse
from src.staged_pilot_v3 import AcceptedResponseContract
from src.temperature_diagnostic import (
    TEMPERATURES,
    TOTAL_CALLS,
    TemperatureDiagnosticError,
    frozen_schedule,
    run_temperature_diagnostic,
    summarize_calls,
    write_new_artifact,
)


class FakeGenerator:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate(self, prompt: str, **kwargs: object) -> GenerationResponse:
        self.calls.append({"prompt": prompt, **kwargs})
        return GenerationResponse(
            expression="(var x1)",
            input_tokens=10,
            output_tokens=2,
            latency_ms=1.5,
            provider_request_count=1,
            seed_supported=False,
            provider_model="kimi-k3",
            finish_reason="stop",
            reasoning_tokens=0,
            candidate_format="json_expression",
        )


def _contract() -> AcceptedResponseContract:
    return AcceptedResponseContract(
        provider_models=("kimi-k3",),
        finish_reasons=("stop", "length"),
        max_output_tokens=256,
        seed_supported=False,
        require_zero_reasoning_tokens=True,
        prompt_cache_mode="absent",
        provider_fingerprint_mode="absent",
    )


def _synthetic_calls(*, reverse: bool = False) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for slot in frozen_schedule():
        temperature = slot["temperature"]
        replicate = slot["replicate_index"]
        if (temperature == 1.2) ^ reverse:
            suffix = str(replicate)
        else:
            suffix = "same"
        rows.append(
            {
                **slot,
                "outer_schema_valid": True,
                "search_valid": True,
                "canonical_hash": f"c-{slot['world_seed']}-{suffix}",
                "behavior_hash": f"b-{slot['world_seed']}-{suffix}",
                "input_tokens": 10,
                "output_tokens": 2,
                "latency_ms": 1.0,
            }
        )
    return rows


class TemperatureDiagnosticTests(unittest.TestCase):
    def test_schedule_is_balanced_and_interleaved(self) -> None:
        schedule = frozen_schedule()
        self.assertEqual(len(schedule), TOTAL_CALLS)
        for seed in (1003, 1001, 1002):
            selected = [row for row in schedule if row["world_seed"] == seed]
            self.assertEqual(
                {temperature: sum(row["temperature"] == temperature for row in selected)
                 for temperature in TEMPERATURES},
                {0.2: 4, 0.7: 4, 1.2: 4},
            )
            self.assertEqual(len(set(row["temperature"] for row in selected[:3])), 3)

    def test_exact_summary_supports_strong_positive_and_reverse(self) -> None:
        positive = summarize_calls(_synthetic_calls())
        self.assertEqual(
            positive["stage_a_classification"],
            "temperature_effect_directionally_supported",
        )
        self.assertEqual(
            positive["canonical_high_vs_low"]["permutation_count"], 343000
        )
        reversed_result = summarize_calls(_synthetic_calls(reverse=True))
        self.assertEqual(
            reversed_result["stage_a_classification"],
            "temperature_effect_directionally_reversed",
        )

    def test_live_core_uses_one_identical_prompt_per_world_and_no_private_output(self) -> None:
        generator = FakeGenerator()
        credentials = ProviderCredentials(
            base_url="https://route.invalid/v3",
            api_key="diagnostic-secret",
            model="kimi-k3",
        )
        with (
            mock.patch(
                "src.temperature_diagnostic._contract_from_canary",
                return_value=(_contract(), "a" * 64),
            ),
            mock.patch(
                "src.temperature_diagnostic.route_binding_sha256",
                return_value="b" * 64,
            ),
        ):
            result = run_temperature_diagnostic(
                credentials, canary_path="unused.json", generator=generator
            )
        self.assertEqual(len(generator.calls), TOTAL_CALLS)
        for start in (0, 12, 24):
            prompts = {call["prompt"] for call in generator.calls[start : start + 12]}
            self.assertEqual(len(prompts), 1)
        self.assertEqual(result["summary"]["overall"]["search_valid_count"], 36)
        self.assertEqual(result["summary"]["stage_a_classification"], "needs_stage_b")
        encoded = json.dumps(result)
        self.assertNotIn("diagnostic-secret", encoded)
        self.assertNotIn("https://route.invalid", encoded)
        self.assertNotIn("(var x1)", encoded)
        self.assertFalse(result["rules"]["private_test_evaluated"])

    def test_publication_is_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            write_new_artifact(path, {"safe": True})
            self.assertEqual(json.loads(path.read_text()), {"safe": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(TemperatureDiagnosticError):
                write_new_artifact(path, {"safe": False})


if __name__ == "__main__":
    unittest.main()
