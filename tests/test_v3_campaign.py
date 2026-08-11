from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from src import v3_campaign
from src.pilot_checkpoint import canonical_json_bytes, sha256_bytes, sha256_json
from src.pilot_checkpoint_v3 import (
    V3_COORDINATOR_VERSION,
    attempt_start_path,
    call_checkpoint_path,
)
from src.runner import GenerationResponse, run_episode
from src.staged_pilot_v3 import FrozenTransactionIdentity
from src.v3_campaign import (
    V3CampaignError,
    acquire_campaign_frontier,
    acquire_campaign_lock,
    build_episode_seal_payload,
    load_campaign_manifest,
    load_compatibility_screen,
    load_episode_seal,
    load_generation_barrier,
    next_shard_frontier,
    publish_campaign_manifest,
    publish_compatibility_screen,
    publish_episode_seal,
    publish_generation_barrier,
)
from src.v3_development import (
    build_campaign_manifest,
    freeze_v3_design,
    load_v3_template,
    transaction_identity_payload,
)
from src.v3_generation import (
    episode_metrics,
    generation_world_view,
    policy_for_entry,
)
from src.verifier import Verifier
from tests.test_v3_development import (
    SOURCE_HASH,
    SOURCE_MANIFEST,
    _bindings,
)


def _write_envelope(path: Path, kind: str, payload: dict) -> dict:
    envelope = {
        "schema_version": 1,
        "kind": kind,
        "payload_sha256": sha256_json(payload),
        "payload": payload,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(envelope) + b"\n")
    os.chmod(path, 0o600)
    return envelope


class V3CampaignStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frozen, cls.plan = freeze_v3_design(
            load_v3_template(),
            model_bindings=_bindings(),
            source_manifest_sha256=SOURCE_HASH,
        )
        cls.manifest_payload = build_campaign_manifest(
            cls.frozen,
            cls.plan,
            source_manifest=SOURCE_MANIFEST,
        )

    def _campaign(self, root: Path) -> dict:
        return publish_campaign_manifest(
            root,
            self.frozen,
            self.plan,
            current_source_manifest=SOURCE_MANIFEST,
        )

    def _write_slot_artifacts(
        self,
        root: Path,
        manifest: dict,
        entry: dict,
        *,
        passing_gate: bool = True,
    ) -> dict:
        identity = FrozenTransactionIdentity(
            **transaction_identity_payload(manifest["payload"], entry)
        )
        response_contract = next(
            item["route_contract"]["accepted_response_contract"]
            for item in manifest["payload"]["route_contracts"]
            if item["model_stratum"] == entry["model_stratum"]
        )
        expressions = ["(var x1)"] * 20
        if entry["arm_id"] == "H" and passing_gate:
            expressions[1::2] = ["(var x2)"] * 10

        class RecordingGenerator:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def generate(self, prompt: str, **kwargs):
                slot = len(self.calls)
                self.calls.append({"prompt": prompt, **kwargs})
                return GenerationResponse(
                    expression=expressions[slot],
                    input_tokens=11,
                    output_tokens=3,
                    latency_ms=1.0,
                    provider_request_count=1,
                    seed_supported=response_contract["seed_supported"],
                    provider_model=response_contract["provider_models"][0],
                    finish_reason=response_contract["finish_reasons"][0],
                    prompt_cache_hit_tokens=None,
                    prompt_cache_miss_tokens=None,
                    reasoning_tokens=0,
                    candidate_format="json_expression",
                    provider_fingerprint=None,
                )

        recorder = RecordingGenerator()
        result = run_episode(
            generation_world_view(entry),
            recorder,
            verifier=Verifier(counterexample_limit=2),
            policy=policy_for_entry(manifest["payload"], entry),
            rounds=5,
            candidates_per_round=4,
            archive_capacity=4,
            max_counterexamples=10,
            seed=1729,
            max_output_tokens=256,
            max_counterexamples_per_round=2,
            evaluate_test=False,
        )
        for slot in range(20):
            request_hash = sha256_json(
                {"shard": entry["shard_index"], "slot": slot, "request": True}
            )
            prompt_hash = sha256_bytes(
                recorder.calls[slot]["prompt"].encode("utf-8")
            )
            start_payload = {
                "shard_index": entry["shard_index"],
                "slot_index": slot,
                "attempt_ordinal": 1,
                "request_body_sha256": request_hash,
                "prompt_sha256": prompt_hash,
                "route_binding_sha256": entry["route_binding_sha256"],
                "transaction_binding_sha256": identity.binding_sha256,
                "coordinator_version": V3_COORDINATOR_VERSION,
            }
            start = _write_envelope(
                attempt_start_path(root, entry["shard_index"], slot, 1),
                "v3-physical-attempt-start",
                start_payload,
            )
            call_payload = {
                "shard_index": entry["shard_index"],
                "slot_index": slot,
                "accepted_attempt": 1,
                "request_body_sha256": request_hash,
                "prompt_sha256": prompt_hash,
                "route_binding_sha256": entry["route_binding_sha256"],
                "transaction_binding_sha256": identity.binding_sha256,
                "accepted_start_payload_sha256": start["payload_sha256"],
                "coordinator_version": V3_COORDINATOR_VERSION,
                "response": {
                    "candidate_expression": expressions[slot],
                    "candidate_parse_status": "canonical_dsl",
                    "candidate_format": "json_expression",
                    "input_tokens": 11,
                    "output_tokens": 3,
                    "latency_ms": 1.0,
                    "accepted_provider_request_count": 1,
                    "seed_supported": response_contract["seed_supported"],
                    "provider_model": response_contract["provider_models"][0],
                    "finish_reason": response_contract["finish_reasons"][0],
                    "prompt_cache_hit_tokens": None,
                    "prompt_cache_miss_tokens": None,
                    "reasoning_tokens": 0,
                    "provider_fingerprint_sha256": None,
                },
            }
            _write_envelope(
                call_checkpoint_path(root, entry["shard_index"], slot),
                "v3-logical-call-checkpoint",
                call_payload,
            )
        return episode_metrics(result, entry)

    def _audit(self, root: Path, entry: dict) -> dict:
        calls = [
            v3_campaign.load_call_checkpoint(root, entry["shard_index"], slot)[
                "payload"
            ]
            for slot in range(20)
        ]
        return v3_campaign._expected_execution_audit(root, entry, calls)

    def _publish_seal(
        self,
        root: Path,
        manifest: dict,
        index: int,
        *,
        passing_gate: bool = True,
    ) -> dict:
        entry = self.plan[index]
        metrics = self._write_slot_artifacts(
            root,
            manifest,
            entry,
            passing_gate=passing_gate,
        )
        return publish_episode_seal(
            root,
            manifest,
            entry,
            episode_metrics=metrics,
            execution_audit=self._audit(root, entry),
        )

    def _publish_gate(
        self,
        root: Path,
        manifest: dict,
        *,
        passing: bool,
    ) -> dict:
        for index in range(8):
            self._publish_seal(
                root,
                manifest,
                index,
                passing_gate=passing,
            )
        return publish_compatibility_screen(root, manifest)

    def test_manifest_is_exclusive_mode_0600_and_source_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            self.assertEqual(manifest["payload"], self.manifest_payload)
            self.assertEqual(
                stat_mode := os.stat(root / "v3-campaign-manifest.json").st_mode
                & 0o777,
                0o600,
            )
            self.assertEqual(
                load_campaign_manifest(
                    root, current_source_manifest=SOURCE_MANIFEST
                )["payload_sha256"],
                manifest["payload_sha256"],
            )
            with self.assertRaises(V3CampaignError):
                self._campaign(root)

            changed = deepcopy(SOURCE_MANIFEST)
            changed["files"][0]["sha256"] = "f" * 64
            changed["source_manifest_sha256"] = sha256_json(changed["files"])
            with self.assertRaisesRegex(V3CampaignError, "current source"):
                load_campaign_manifest(root, current_source_manifest=changed)

            os.chmod(root / "v3-campaign-manifest.json", 0o644)
            with self.assertRaises(V3CampaignError):
                load_campaign_manifest(root, current_source_manifest=SOURCE_MANIFEST)
            self.assertEqual(stat_mode & 0o777, 0o600)

    def test_global_lock_is_nonblocking_and_frontier_is_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            with acquire_campaign_lock(root):
                with self.assertRaisesRegex(V3CampaignError, "already owned"):
                    with acquire_campaign_lock(root):
                        pass
            self.assertEqual(next_shard_frontier(root, manifest), 0)
            with self.assertRaisesRegex(V3CampaignError, "strict frontier"):
                with acquire_campaign_frontier(root, manifest, 1):
                    pass
            lock = root / "campaign-locks" / "global.lock"
            self.assertEqual(os.stat(lock).st_mode & 0o777, 0o600)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            fake = v3_campaign.episode_seal_path(root, 0)
            fake.parent.mkdir(parents=True)
            fake.write_text("junk", encoding="utf-8")
            os.chmod(fake, 0o600)
            with self.assertRaisesRegex(V3CampaignError, "cannot load episode seal"):
                next_shard_frontier(root, manifest)

    def test_seal_binds_file_and_payload_hashes_and_rejects_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            envelope = self._publish_seal(root, manifest, 0)
            payload = envelope["payload"]
            self.assertEqual(len(payload["ordered_call_checkpoints"]), 20)
            self.assertEqual(
                len(payload["ordered_call_checkpoint_payload_sha256"]), 20
            )
            self.assertNotIn("call_checkpoint_replays", payload["execution_audit"])
            self.assertNotIn("logical_calls_seen", payload["execution_audit"])
            self.assertNotIn(
                "physical_request_start_markers", payload["execution_audit"]
            )
            encoded = json.dumps(payload, sort_keys=True).lower()
            for forbidden in (
                "candidate_expression",
                "canonical_hash",
                "behavior_hash",
                "prompt_sha256",
                "endpoint",
                "raw_response",
            ):
                self.assertNotIn(forbidden, encoded)
            self.assertEqual(load_episode_seal(root, manifest, 0), envelope)

            checkpoint = call_checkpoint_path(root, 0, 0)
            checkpoint.write_bytes(checkpoint.read_bytes() + b"\n")
            with self.assertRaisesRegex(V3CampaignError, "file hashes"):
                load_episode_seal(root, manifest, 0)

    def test_seal_rejects_forged_generation_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            entry = self.plan[0]
            metrics = self._write_slot_artifacts(root, manifest, entry)
            metrics["canonical_unique_count"] += 1
            with self.assertRaisesRegex(V3CampaignError, "deterministic"):
                publish_episode_seal(
                    root,
                    manifest,
                    entry,
                    episode_metrics=metrics,
                    execution_audit=self._audit(root, entry),
                )

    def test_gate_screen_blocks_main_until_pass_and_failure_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            for index in range(8):
                self._publish_seal(root, manifest, index)
            with self.assertRaisesRegex(V3CampaignError, "passing compatibility"):
                with acquire_campaign_frontier(root, manifest, 8):
                    pass
            screen = publish_compatibility_screen(root, manifest)
            self.assertEqual(screen["payload"]["status"], "passed")
            self.assertEqual(load_compatibility_screen(root, manifest), screen)
            self.assertEqual(next_shard_frontier(root, manifest), 8)
            with acquire_campaign_frontier(root, manifest, 8):
                pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            screen = self._publish_gate(root, manifest, passing=False)
            self.assertEqual(
                screen["payload"]["status"],
                "compatibility_screen_failed",
            )
            with self.assertRaisesRegex(V3CampaignError, "passing compatibility"):
                with acquire_campaign_frontier(root, manifest, 8):
                    pass

    def test_generation_barrier_reenumerates_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._campaign(root)
            screen = self._publish_gate(root, manifest, passing=True)
            self.assertEqual(screen["payload"]["status"], "passed")
            for index in range(8, 104):
                entry = self.plan[index]
                metrics = self._write_slot_artifacts(root, manifest, entry)
                payload = build_episode_seal_payload(
                    root,
                    manifest,
                    entry,
                    episode_metrics=metrics,
                    execution_audit=self._audit(root, entry),
                )
                _write_envelope(
                    v3_campaign.episode_seal_path(root, index),
                    v3_campaign.EPISODE_SEAL_KIND,
                    payload,
                )
            barrier = publish_generation_barrier(root, manifest)
            self.assertEqual(barrier["payload"]["main_shard_count"], 96)
            self.assertEqual(
                barrier["payload"]["main_logical_calls_completed"], 1920
            )
            self.assertEqual(load_generation_barrier(root, manifest), barrier)

            seal_path = v3_campaign.episode_seal_path(root, 103)
            seal_path.write_bytes(seal_path.read_bytes() + b"\n")
            with self.assertRaisesRegex(V3CampaignError, "differs"):
                load_generation_barrier(root, manifest)


if __name__ == "__main__":
    unittest.main()
