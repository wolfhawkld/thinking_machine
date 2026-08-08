from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.credentials import ProviderCredentials
from src.provider_canary import _write_new_json, run_canary
from src.runner import GenerationResponse


ROOT = Path(__file__).resolve().parents[1]
SECRET = "canary-test-secret"


class FakeGenerator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, prompt: str, **kwargs) -> GenerationResponse:
        self.calls.append({"prompt": prompt, **kwargs})
        return GenerationResponse(
            expression="(var x1)",
            input_tokens=321,
            output_tokens=9,
            latency_ms=123.5,
            provider_request_count=1,
            seed_supported=False,
            provider_model="deepseek-v4-flash",
            finish_reason="stop",
        )


class ProviderCanaryTests(unittest.TestCase):
    def credentials(self) -> ProviderCredentials:
        return ProviderCredentials(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key=SECRET,
        )

    def test_canary_uses_one_exact_experiment_request_and_is_secret_free(self) -> None:
        generator = FakeGenerator()
        result = run_canary(
            self.credentials(),
            config_path=ROOT / "configs" / "pilot.json",
            generator=generator,
        )

        self.assertEqual(len(generator.calls), 1)
        call = generator.calls[0]
        self.assertEqual(call["temperature"], 0.7)
        self.assertEqual(call["max_output_tokens"], 128)
        self.assertEqual(call["seed"], 1729)
        self.assertIn("Observed training examples", call["prompt"])
        self.assertFalse(result["evidence"])
        self.assertEqual(result["provider"]["thinking"], "disabled")
        self.assertFalse(result["provider"]["seed_sent"])
        self.assertEqual(result["response"]["provider_model"], "deepseek-v4-flash")
        self.assertTrue(result["verification"]["syntax_valid"])
        self.assertNotIn(SECRET, json.dumps(result, sort_keys=True))

    def test_model_mismatch_is_rejected_before_generation(self) -> None:
        credentials = ProviderCredentials(
            base_url="https://api.deepseek.com",
            model="other-model",
            api_key=SECRET,
        )
        generator = FakeGenerator()
        with self.assertRaisesRegex(ValueError, "does not match"):
            run_canary(
                credentials,
                config_path=ROOT / "configs" / "pilot.json",
                generator=generator,
            )
        self.assertEqual(generator.calls, [])

    def test_artifact_writer_never_overwrites(self) -> None:
        result = {"safe": True}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "canary.json"
            _write_new_json(path, result)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)
            with self.assertRaises(FileExistsError):
                _write_new_json(path, result)


if __name__ == "__main__":
    unittest.main()
