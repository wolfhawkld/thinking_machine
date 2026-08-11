from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src.runner import SmokeTestGenerator, run_episode
from src.v3_campaign import load_episode_seal, publish_campaign_manifest
from src.v3_development import build_campaign_manifest, freeze_v3_design, load_v3_template
from src.v3_generation import (
    V3GenerationError,
    accepted_response_contract,
    bind_live_generator,
    episode_metrics,
    generation_state_sha256,
    generation_world_view,
    policy_for_entry,
    run_next_v3_generation_shard,
    stable_execution_audit,
)
from src.verifier import Verifier
from tests.test_v3_development import SOURCE_HASH, SOURCE_MANIFEST, _bindings, _generator


class _FixtureHTTPResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        return False

    def getcode(self) -> int:
        return 200

    def read(self) -> bytes:
        return json.dumps(
            {
                "model": "response-deepseek-v4-snapshot",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps({"expression": "(var x1)"}),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 3,
                    "total_tokens": 14,
                },
            }
        ).encode("utf-8")


class _FixtureOpener:
    def __init__(self, *, allow_network: bool = True) -> None:
        self.allow_network = allow_network
        self.calls = []

    def open(self, request, timeout):
        if not self.allow_network:
            raise AssertionError("offline replay attempted a network request")
        self.calls.append((request, timeout))
        return _FixtureHTTPResponse()


class V3GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frozen, cls.plan = freeze_v3_design(
            load_v3_template(),
            model_bindings=_bindings(),
            source_manifest_sha256=SOURCE_HASH,
        )
        cls.manifest = build_campaign_manifest(
            cls.frozen,
            cls.plan,
            source_manifest=SOURCE_MANIFEST,
        )

    def _entry(self, arm_id: str = "E2"):
        return next(
            entry
            for entry in self.plan
            if entry["phase"] == "main" and entry["arm_id"] == arm_id
        )

    def _live_generator(self, opener: _FixtureOpener):
        generator = _generator(
            base_url="https://deepseek-route.invalid/v1",
            model="request-deepseek-v4",
        )
        generator._transport._opener = opener
        return generator

    def _episode(self, arm_id: str = "E2", *, invalid: bool = False):
        entry = self._entry(arm_id)
        world = generation_world_view(entry)
        script = (
            [{"expression": "not valid dsl"}] * 20 if invalid else None
        )
        result = run_episode(
            world,
            SmokeTestGenerator(script=script),
            verifier=Verifier(counterexample_limit=2),
            policy=policy_for_entry(self.manifest, entry),
            rounds=5,
            candidates_per_round=4,
            archive_capacity=4,
            max_counterexamples=10,
            max_output_tokens=256,
            max_counterexamples_per_round=2,
            seed=1729,
            evaluate_test=False,
        )
        return entry, world, result

    def test_generation_view_has_no_test_or_hidden_law_surface(self) -> None:
        entry = self._entry()
        world = generation_world_view(entry)
        self.assertEqual(world.world_hash, entry["world_hash"])
        self.assertEqual((len(world.train), len(world.probe)), (12, 12))
        self.assertFalse(hasattr(world, "test"))
        self.assertFalse(hasattr(world, "law"))
        self.assertFalse(hasattr(world, "X_test"))

    def test_manifest_constructs_exact_contract_and_transaction(self) -> None:
        entry = next(
            item
            for item in self.plan
            if item["model_stratum"] == "official-deepseek-v4"
        )
        generator = _generator(
            base_url="https://deepseek-route.invalid/v1",
            model="request-deepseek-v4",
        )
        contract, identity = bind_live_generator(self.manifest, entry, generator)
        self.assertEqual(contract.provider_models, ("response-deepseek-v4-snapshot",))
        self.assertEqual(identity.shard_index, entry["shard_index"])
        self.assertEqual(identity.plan_entry_sha256, entry["plan_entry_sha256"])
        self.assertEqual(
            accepted_response_contract(
                self.manifest, "official-deepseek-v4"
            ),
            contract,
        )

        changed = _generator(
            base_url="https://changed.invalid/v1",
            model="request-deepseek-v4",
        )
        with self.assertRaisesRegex(V3GenerationError, "route"):
            bind_live_generator(self.manifest, entry, changed)

    def test_episode_metrics_are_scalar_safe_and_digest_replay_state(self) -> None:
        entry, _, result = self._episode()
        metrics = episode_metrics(result, entry)
        self.assertEqual(metrics["planned_candidate_count"], 20)
        self.assertEqual(metrics["completed_candidate_count"], 20)
        self.assertEqual(len(metrics["controller_trace"]), 5)
        self.assertFalse(metrics["private_test_evaluated"])
        self.assertEqual(
            metrics["generation_state_sha256"], generation_state_sha256(result)
        )
        encoded = repr(metrics).lower()
        for forbidden in (
            "candidate_expression",
            "canonical_hash",
            "behavior_hash",
            "prediction",
            "test_examples",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_all_invalid_episode_is_a_complete_generation_outcome(self) -> None:
        entry, _, result = self._episode(invalid=True)
        metrics = episode_metrics(result, entry)
        self.assertEqual(metrics["search_valid_count"], 0)
        self.assertTrue(metrics["all_invalid"])
        self.assertFalse(metrics["selection_exists"])
        self.assertIsNone(metrics["selected_probe_score"])

    def test_metrics_reject_private_test_or_wrong_budget(self) -> None:
        entry, _, result = self._episode("C")
        result.final_test = Verifier().verify_test(
            result.final_candidate.candidate, tuple(result.world.probe)
        )
        with self.assertRaisesRegex(V3GenerationError, "private-test"):
            episode_metrics(result, entry)
        result.final_test = None
        result.rounds[-1].pop()
        with self.assertRaisesRegex(V3GenerationError, "5x4"):
            episode_metrics(result, entry)

    def test_execution_audit_normalization_is_replay_invariant(self) -> None:
        audit = {
            "estimand": "first_durably_recorded_http_success",
            "logical_calls_seen": 20,
            "durable_logical_call_checkpoints": 20,
            "shard_complete": True,
            "physical_request_starts": 21,
            "physical_request_start_markers": 21,
            "start_markers_are_not_confirmed_provider_receipts": True,
            "slots_with_retry": 1,
            "retry_count": 1,
            "outcome_class_counts": {"retryable_http": 1},
            "failure_category_counts": {},
            "http_status_counts": {"503": 1},
            "unresolved_slot_count": 0,
            "exhausted_slot_count": 0,
            "fatal_slot_count": 0,
            "ready_for_retry_slot_count": 0,
            "accepted_attempt_ordinals": [2] + [1] * 19,
            "call_checkpoint_replays": 7,
            "content_retry_count": 0,
            "accepted_known_input_tokens": 200,
            "accepted_known_output_tokens": 100,
            "accepted_known_latency_ms": 123.0,
            "discarded_known_response_count": 0,
            "discarded_known_input_tokens": 0,
            "discarded_known_output_tokens": 0,
            "discarded_known_latency_ms": 0.0,
            "known_usage_response_count": 20,
            "usage_unknown_start_marker_count": 1,
            "gross_known_token_lower_bound": 300,
            "gross_known_latency_ms": 123.0,
            "gross_usage_complete": False,
            "recovery_allows_actual_token_matched_claim": False,
        }
        normalized = stable_execution_audit(audit)
        self.assertNotIn("logical_calls_seen", normalized)
        self.assertNotIn("call_checkpoint_replays", normalized)
        self.assertNotIn("physical_request_start_markers", normalized)
        audit["call_checkpoint_replays"] = 20
        self.assertEqual(stable_execution_audit(audit), normalized)

        audit["fatal_slot_count"] = 1
        with self.assertRaisesRegex(V3GenerationError, "unsafe terminal"):
            stable_execution_audit(audit)

    def test_frontier_coordinator_runs_exactly_one_generation_only_shard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = publish_campaign_manifest(
                root,
                self.frozen,
                self.plan,
                current_source_manifest=SOURCE_MANIFEST,
            )
            opener = _FixtureOpener()
            generator = self._live_generator(opener)
            status = run_next_v3_generation_shard(
                root,
                generator,
                current_source_manifest=SOURCE_MANIFEST,
            )
            self.assertEqual(
                status,
                {
                    "status": "gate_in_progress",
                    "sealed_episode_count": 1,
                    "next_shard_index": 1,
                    "private_test_evaluated": False,
                },
            )
            self.assertEqual(len(opener.calls), 20)
            seal = load_episode_seal(root, manifest, 0)["payload"]
            self.assertFalse(seal["private_test_evaluated"])
            self.assertEqual(seal["logical_calls_completed"], 20)

    def test_completed_calls_replay_without_network_after_preseal_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = publish_campaign_manifest(
                root,
                self.frozen,
                self.plan,
                current_source_manifest=SOURCE_MANIFEST,
            )
            first_opener = _FixtureOpener()
            with mock.patch(
                "src.v3_campaign.publish_episode_seal",
                side_effect=RuntimeError("simulated crash before seal"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                    run_next_v3_generation_shard(
                        root,
                        self._live_generator(first_opener),
                        current_source_manifest=SOURCE_MANIFEST,
                    )
            self.assertEqual(len(first_opener.calls), 20)

            offline_opener = _FixtureOpener(allow_network=False)
            status = run_next_v3_generation_shard(
                root,
                self._live_generator(offline_opener),
                current_source_manifest=SOURCE_MANIFEST,
            )
            self.assertEqual(status["sealed_episode_count"], 1)
            self.assertEqual(offline_opener.calls, [])
            self.assertEqual(
                load_episode_seal(root, manifest, 0)["payload"][
                    "logical_calls_completed"
                ],
                20,
            )


if __name__ == "__main__":
    unittest.main()
