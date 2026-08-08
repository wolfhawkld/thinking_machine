from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest

from src.credentials import ProviderCredentials
from src.development_pilot import (
    DEVELOPMENT_PILOT_MODEL,
    DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
    DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL,
)
from src.pilot_checkpoint import load_shard_checkpoint
from src.providers import ResponsePayloadError, TransportError
from src.runner import GenerationResponse
from src.staged_pilot import (
    StagedPilotError,
    _execution_audit,
    finalize_snapshot,
    run_next_shard,
    run_stage,
)
from src.staged_pilot_analysis import analyze_staged_snapshot


SECRET = "staged-pilot-test-secret"
PROVENANCE = {
    "schema_version": 1,
    "created_at_utc": "2026-08-08T00:00:00+00:00",
    "source_manifest_sha256": "a" * 64,
    "files": [],
    "environment": {"git_head": "b" * 40},
}


def credentials() -> ProviderCredentials:
    return ProviderCredentials(
        base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
        model=DEVELOPMENT_PILOT_MODEL,
        api_key=SECRET,
    )


class FakeGenerator:
    def __init__(self, factory: "FakeFactory") -> None:
        self.factory = factory

    def generate(self, prompt: str, **kwargs: object) -> GenerationResponse:
        del prompt, kwargs
        index = self.factory.calls
        self.factory.calls += 1
        if self.factory.failure is not None and index == self.factory.fail_at:
            raise self.factory.failure
        return GenerationResponse(
            expression="(var x1)",
            input_tokens=10,
            output_tokens=2,
            latency_ms=3.5,
            provider_request_count=1,
            seed_supported=False,
            provider_model=DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL,
            provider_fingerprint=None,
            finish_reason="stop",
            prompt_cache_hit_tokens=None,
            prompt_cache_miss_tokens=None,
            reasoning_tokens=0,
            candidate_format="json_expression",
        )


class FakeFactory:
    def __init__(
        self,
        *,
        failure: BaseException | None = None,
        fail_at: int = -1,
    ) -> None:
        self.calls = 0
        self.failure = failure
        self.fail_at = fail_at

    def __call__(self, context: object) -> FakeGenerator:
        del context
        return FakeGenerator(self)


class StagedPilotTests(unittest.TestCase):
    def test_s1_is_committed_then_finalized_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            factory = FakeFactory()
            state = run_stage(
                directory,
                credentials(),
                generator_factory=factory,
                provenance_manifest=PROVENANCE,
                progress_stream=io.StringIO(),
            )
            self.assertEqual(state["next_shard_index"], 14)
            self.assertEqual(factory.calls, 280)
            checkpoint = load_shard_checkpoint(directory, 0)
            self.assertNotIn("final_test", checkpoint["payload"]["run"])

            snapshot = finalize_snapshot(
                directory,
                2,
                current_source_manifest=PROVENANCE,
            )

            self.assertEqual(factory.calls, 280)
            self.assertEqual(len(snapshot["runs"]), 14)
            self.assertEqual(
                snapshot["stage"]["private_test_release_rule"],
                "after_all_required_checkpoints_and_world_seals_verified",
            )
            self.assertNotIn("candidate_expression", repr(snapshot))
            analysis = analyze_staged_snapshot(snapshot)
            self.assertTrue(analysis["engineering"]["passed"])
            self.assertEqual(analysis["classification"], "interim_descriptive_only")

    def test_delivery_ambiguous_failure_allows_explicit_whole_shard_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = FakeFactory(
                failure=TransportError(category="timeout", delivery_ambiguous=True),
                fail_at=2,
            )
            with self.assertRaises(TransportError):
                run_next_shard(
                    directory,
                    credentials(),
                    generator_factory=first,
                    provenance_manifest=PROVENANCE,
                    progress_stream=io.StringIO(),
                )
            replacement = FakeFactory()
            state = run_next_shard(
                directory,
                credentials(),
                generator_factory=replacement,
                provenance_manifest=PROVENANCE,
                resume=True,
                progress_stream=io.StringIO(),
            )
            self.assertEqual(state["attempt_number"], 2)
            self.assertEqual(replacement.calls, 20)
            checkpoint = load_shard_checkpoint(directory, 0)
            self.assertEqual(checkpoint["payload"]["attempt_number"], 2)
            audit = _execution_audit(Path(directory), [checkpoint])
            self.assertEqual(audit["physical_request_starts"], 23)
            self.assertEqual(audit["discarded_operational_calls"], 2)
            self.assertEqual(audit["ambiguous_operational_calls"], 1)
            self.assertFalse(audit["gross_usage_complete"])
            self.assertTrue(audit["recovery_used"])

    def test_response_contract_failure_is_campaign_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = FakeFactory(
                failure=ResponsePayloadError("malformed response"),
                fail_at=0,
            )
            with self.assertRaises(ResponsePayloadError):
                run_next_shard(
                    directory,
                    credentials(),
                    generator_factory=first,
                    provenance_manifest=PROVENANCE,
                    progress_stream=io.StringIO(),
                )
            replacement = FakeFactory()
            with self.assertRaisesRegex(StagedPilotError, "campaign-fatal"):
                run_next_shard(
                    directory,
                    credentials(),
                    generator_factory=replacement,
                    provenance_manifest=PROVENANCE,
                    resume=True,
                    progress_stream=io.StringIO(),
                )
            self.assertEqual(replacement.calls, 0)


if __name__ == "__main__":
    unittest.main()
