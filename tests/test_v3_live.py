import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.credentials import ProviderCredentials
from src.pilot_checkpoint import canonical_json_bytes
from src.providers.openai_compatible import OpenAICompatibleGenerator
from src.runner import GenerationResponse
from src.v3_campaign import load_campaign_manifest
from src.v3_live import (
    V3LiveError,
    _write_new_json,
    build_v3_generator,
    initialize_v3_campaign,
    main,
    model_binding_from_canary,
    run_live_campaign,
    run_next_live_shard,
    run_route_canary,
)


def _credentials(model: str, *, suffix: str = "") -> ProviderCredentials:
    return ProviderCredentials(
        base_url=f"https://provider{suffix}.example/v1",
        model=model,
        api_key=f"secret-{model}",
    )


def _response(
    model: str,
    *,
    fingerprint: str | None = None,
    cache: bool = False,
    finish_reason: str = "stop",
) -> GenerationResponse:
    return GenerationResponse(
        expression="(var x1)",
        input_tokens=10,
        output_tokens=2,
        latency_ms=1.5,
        provider_request_count=1,
        seed_supported=False,
        provider_model=f"{model}-snapshot",
        finish_reason=finish_reason,
        prompt_cache_hit_tokens=4 if cache else None,
        prompt_cache_miss_tokens=6 if cache else None,
        reasoning_tokens=0,
        candidate_format="json_expression",
        provider_fingerprint=fingerprint,
    )


def _run_canary(
    credentials: ProviderCredentials,
    stratum: str,
    *,
    fingerprints: list[str | None] | None = None,
) -> dict:
    values = fingerprints if fingerprints is not None else [None] * 8
    calls = 0

    def generate(_self, _prompt, **_kwargs):
        nonlocal calls
        result = _response(credentials.model, fingerprint=values[calls])
        calls += 1
        return result

    with mock.patch.object(OpenAICompatibleGenerator, "generate", new=generate):
        artifact = run_route_canary(
            credentials,
            provider=f"provider-{stratum}",
            stratum_id=stratum,
        )
    assert calls == 8
    return artifact


class V3RouteCanaryTests(unittest.TestCase):
    def test_canary_is_eight_call_generation_only_and_secret_free(self) -> None:
        credentials = _credentials("deepseek-v4-flash")
        artifact = _run_canary(credentials, "official-deepseek-v4")
        self.assertTrue(artifact["passed"])
        self.assertEqual(artifact["protocol"]["logical_calls"], 8)
        self.assertFalse(artifact["protocol"]["private_test_evaluated"])
        self.assertEqual(artifact["diagnostics"]["search_valid_count"], 8)
        self.assertEqual(
            artifact["accepted_response_contract"]["prompt_cache_mode"],
            "absent",
        )
        encoded = canonical_json_bytes(artifact).decode("utf-8")
        self.assertNotIn(credentials.api_key, encoded)
        self.assertNotIn(credentials.base_url, encoded)
        self.assertNotIn("(var x1)", encoded)

    def test_canary_freezes_stable_fingerprint(self) -> None:
        credentials = _credentials("deepseek-v4-flash")
        artifact = _run_canary(
            credentials,
            "official-deepseek-v4",
            fingerprints=["stable-fingerprint"] * 8,
        )
        response = artifact["accepted_response_contract"]
        self.assertEqual(response["provider_fingerprint_mode"], "exact_sha256")
        self.assertEqual(len(response["provider_fingerprint_sha256"]), 64)

    def test_canary_rejects_unstable_fingerprint(self) -> None:
        credentials = _credentials("deepseek-v4-flash")
        with self.assertRaisesRegex(V3LiveError, "fingerprint"):
            _run_canary(
                credentials,
                "official-deepseek-v4",
                fingerprints=["first"] * 7 + ["second"],
            )

    def test_length_finish_is_a_paid_content_result_not_campaign_fatal(self) -> None:
        credentials = _credentials("deepseek-v4-flash")
        calls = 0

        def generate(_self, _prompt, **_kwargs):
            nonlocal calls
            result = _response(
                credentials.model,
                finish_reason="length" if calls == 3 else "stop",
            )
            calls += 1
            return result

        with mock.patch.object(OpenAICompatibleGenerator, "generate", new=generate):
            artifact = run_route_canary(
                credentials,
                provider="provider",
                stratum_id="official-deepseek-v4",
            )
        self.assertTrue(artifact["passed"])
        self.assertEqual(
            artifact["accepted_response_contract"]["finish_reasons"],
            ["stop", "length"],
        )

    def test_binding_requires_same_current_route(self) -> None:
        credentials = _credentials("deepseek-v4-flash")
        artifact = _run_canary(credentials, "official-deepseek-v4")
        with tempfile.TemporaryDirectory() as directory:
            path = _write_new_json(Path(directory) / "canary.json", artifact)
            binding = model_binding_from_canary(
                path,
                credentials,
                expected_stratum_id="official-deepseek-v4",
            )
            self.assertEqual(binding["name"], credentials.model)
            self.assertEqual(binding["snapshot"], "deepseek-v4-flash-snapshot")
            self.assertTrue(binding["canary_evidence"]["contract_satisfied"])
            with self.assertRaisesRegex(V3LiveError, "current route"):
                model_binding_from_canary(
                    path,
                    _credentials("deepseek-v4-flash", suffix="-changed"),
                    expected_stratum_id="official-deepseek-v4",
                )


class V3LiveCampaignTests(unittest.TestCase):
    def test_two_canaries_initialize_bound_campaign(self) -> None:
        deepseek = _credentials("deepseek-v4-flash")
        kimi = _credentials("kimi-k3", suffix="-kimi")
        deepseek_artifact = _run_canary(deepseek, "official-deepseek-v4")
        kimi_artifact = _run_canary(kimi, "volcengine-kimi-k3")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deepseek_path = _write_new_json(
                root / "deepseek-canary.json", deepseek_artifact
            )
            kimi_path = _write_new_json(
                root / "kimi-canary.json", kimi_artifact
            )
            campaign = root / "campaign"
            status = initialize_v3_campaign(
                campaign,
                deepseek_credentials=deepseek,
                deepseek_canary=deepseek_path,
                kimi_credentials=kimi,
                kimi_canary=kimi_path,
            )
            self.assertEqual(status["status"], "campaign_initialized")
            manifest = load_campaign_manifest(campaign)["payload"]
            self.assertEqual(len(manifest["execution_plan"]), 104)
            self.assertEqual(
                {item["stratum_id"] for item in manifest["frozen_config"]["model_strata"]},
                {"official-deepseek-v4", "volcengine-kimi-k3"},
            )

    def test_run_next_selects_frontier_model(self) -> None:
        deepseek = _credentials("deepseek-v4-flash")
        kimi = _credentials("kimi-k3", suffix="-kimi")
        envelope = {
            "payload": {
                "execution_plan": [
                    {"model_stratum": "volcengine-kimi-k3"},
                ]
            }
        }

        def run(_campaign, generator, **_kwargs):
            self.assertEqual(generator.model, kimi.model)
            return {"status": "gate_in_progress"}

        with (
            mock.patch("src.v3_live.load_campaign_manifest", return_value=envelope),
            mock.patch("src.v3_live.next_shard_frontier", return_value=0),
            mock.patch("src.v3_live.run_next_v3_generation_shard", side_effect=run),
        ):
            status = run_next_live_shard(
                "campaign",
                deepseek_credentials=deepseek,
                kimi_credentials=kimi,
            )
        self.assertEqual(status["status"], "gate_in_progress")

    def test_gate_stop_repairs_missing_screen_without_starting_main(self) -> None:
        deepseek = _credentials("deepseek-v4-flash")
        kimi = _credentials("kimi-k3", suffix="-kimi")
        envelope = {"payload": {"execution_plan": []}}
        screen = {"payload": {"status": "passed"}}
        lease = object()
        with (
            mock.patch("src.v3_live.load_campaign_manifest", return_value=envelope),
            mock.patch("src.v3_live.next_shard_frontier", return_value=8),
            mock.patch(
                "src.v3_live.compatibility_screen_path",
                return_value=mock.Mock(exists=mock.Mock(return_value=False)),
            ),
            mock.patch(
                "src.v3_live.acquire_campaign_lock"
            ) as acquire,
            mock.patch("src.v3_live.publish_compatibility_screen") as publish,
            mock.patch(
                "src.v3_live.load_compatibility_screen", return_value=screen
            ),
            mock.patch("src.v3_live.run_next_live_shard") as run_next,
        ):
            acquire.return_value.__enter__.return_value = lease
            status = run_live_campaign(
                "campaign",
                deepseek_credentials=deepseek,
                kimi_credentials=kimi,
            )
        publish.assert_called_once_with("campaign", envelope, lease=lease)
        run_next.assert_not_called()
        self.assertEqual(status["status"], "compatibility_screen_passed")
        self.assertEqual(status["next_shard_index"], 8)

    def test_cli_refuses_paid_canary_without_execute(self) -> None:
        with self.assertRaisesRegex(V3LiveError, "--execute"):
            main(
                [
                    "canary",
                    "--stratum",
                    "official-deepseek-v4",
                    "--provider",
                    "provider",
                    "--env-file",
                    "missing.env",
                    "--env-prefix",
                    "DEEPSEEK",
                    "--output",
                    "unused.json",
                ]
            )


if __name__ == "__main__":
    unittest.main()
