from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from src.pilot_checkpoint import INVALID_CANDIDATE_SENTINEL, PilotCheckpointError
from src.pilot_checkpoint_v3 import (
    PilotCheckpointV3Error,
    acquire_shard_lock,
    attempt_outcome_path,
    attempt_start_path,
    call_checkpoint_path,
    inspect_slot_state,
    load_attempt_outcome,
    load_attempt_start,
    load_call_checkpoint,
    publish_attempt_outcome,
    publish_attempt_start,
    publish_call_checkpoint,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


def _start(root: Path, attempt: int) -> dict:
    return publish_attempt_start(
        root,
        shard_index=7,
        slot_index=3,
        attempt_ordinal=attempt,
        request_body_sha256=HASH_A,
        prompt_sha256=HASH_B,
        route_binding_sha256=HASH_C,
        transaction_binding_sha256=HASH_D,
    )


def _response(expression: str = "(var x1)") -> dict:
    return {
        "candidate_expression": expression,
        "candidate_parse_status": (
            "invalid_candidate"
            if expression == INVALID_CANDIDATE_SENTINEL
            else "canonical_dsl"
        ),
        "candidate_format": "json_expression",
        "input_tokens": 10,
        "output_tokens": 4,
        "latency_ms": 125.0,
        "accepted_provider_request_count": 1,
        "seed_supported": False,
        "provider_model": "model-snapshot",
        "finish_reason": "stop",
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 10,
        "reasoning_tokens": 0,
        "provider_fingerprint_sha256": None,
    }


def _call_payload(start: dict, *, attempt: int, expression: str = "(var x1)") -> dict:
    return {
        "shard_index": 7,
        "slot_index": 3,
        "accepted_attempt": attempt,
        "request_body_sha256": HASH_A,
        "prompt_sha256": HASH_B,
        "route_binding_sha256": HASH_C,
        "transaction_binding_sha256": HASH_D,
        "accepted_start_payload_sha256": start["payload_sha256"],
        "coordinator_version": "logical-slot-recovery-v1",
        "response": _response(expression),
    }


class PilotCheckpointV3Tests(unittest.TestCase):
    def test_retry_then_success_is_an_immutable_validated_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _start(root, 1)
            self.assertEqual(inspect_slot_state(root, 7, 3).status, "unresolved")
            publish_attempt_outcome(
                root,
                shard_index=7,
                slot_index=3,
                attempt_ordinal=1,
                outcome_class="retryable_transport",
                failure_category="timeout",
            )
            ready = inspect_slot_state(root, 7, 3)
            self.assertEqual((ready.status, ready.next_attempt), ("ready_for_retry", 2))
            second = _start(root, 2)
            publish_call_checkpoint(root, _call_payload(second, attempt=2))

            committed = inspect_slot_state(root, 7, 3)
            self.assertEqual(committed.status, "committed")
            self.assertEqual(committed.started_attempts, (1, 2))
            self.assertEqual(committed.retryable_outcomes, (1,))
            self.assertEqual(committed.accepted_attempt, 2)
            self.assertEqual(load_call_checkpoint(root, 7, 3)["payload"]["response"]["candidate_expression"], "(var x1)")
            for path in (
                attempt_start_path(root, 7, 3, 1),
                attempt_outcome_path(root, 7, 3, 1),
                attempt_start_path(root, 7, 3, 2),
                call_checkpoint_path(root, 7, 3),
            ):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(PilotCheckpointV3Error):
                _start(root, 3)
            self.assertEqual(first["payload"]["request_body_sha256"], second["payload"]["request_body_sha256"])

    def test_only_closed_retry_classes_authorize_the_next_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _start(root, 1)
            with self.assertRaises(PilotCheckpointV3Error):
                publish_attempt_outcome(
                    root,
                    shard_index=7,
                    slot_index=3,
                    attempt_ordinal=1,
                    outcome_class="retryable_transport",
                    failure_category="injected_transport_exception",
                )
            publish_attempt_outcome(
                root,
                shard_index=7,
                slot_index=3,
                attempt_ordinal=1,
                outcome_class="retryable_http",
                http_status=429,
            )
            self.assertEqual(inspect_slot_state(root, 7, 3).next_attempt, 2)
            with self.assertRaisesRegex(PilotCheckpointV3Error, "changed"):
                publish_attempt_start(
                    root,
                    shard_index=7,
                    slot_index=3,
                    attempt_ordinal=2,
                    request_body_sha256="d" * 64,
                    prompt_sha256=HASH_B,
                    route_binding_sha256=HASH_C,
                    transaction_binding_sha256=HASH_D,
                )

    def test_three_retryable_failures_exhaust_without_a_scientific_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for attempt in (1, 2, 3):
                _start(root, attempt)
                publish_attempt_outcome(
                    root,
                    shard_index=7,
                    slot_index=3,
                    attempt_ordinal=attempt,
                    outcome_class="retryable_http",
                    http_status=503,
                )
            state = inspect_slot_state(root, 7, 3)
            self.assertEqual(state.status, "exhausted")
            self.assertIsNone(state.next_attempt)
            self.assertFalse(call_checkpoint_path(root, 7, 3).exists())

    def test_orphaned_start_is_unresolved_and_never_auto_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _start(root, 1)
            state = inspect_slot_state(root, 7, 3)
            self.assertEqual(state.status, "unresolved")
            with self.assertRaisesRegex(PilotCheckpointV3Error, "not authorized"):
                _start(root, 2)

    def test_invalid_content_is_durably_accepted_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = _start(root, 1)
            publish_call_checkpoint(
                root,
                _call_payload(
                    start,
                    attempt=1,
                    expression=INVALID_CANDIDATE_SENTINEL,
                ),
            )
            self.assertEqual(inspect_slot_state(root, 7, 3).status, "committed")
            self.assertEqual(inspect_slot_state(root, 7, 3).started_attempts, (1,))

    def test_tamper_gap_and_mismatched_binding_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = _start(root, 1)
            payload = _call_payload(start, attempt=1)
            payload["request_body_sha256"] = "d" * 64
            with self.assertRaisesRegex(PilotCheckpointV3Error, "mismatches"):
                publish_call_checkpoint(root, payload)

            path = attempt_start_path(root, 7, 3, 1)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["payload"]["slot_index"] = 4
            path.write_text(json.dumps(value), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(PilotCheckpointError, "hash mismatch"):
                inspect_slot_state(root, 7, 3)

    def test_hash_valid_envelopes_cannot_be_copied_to_other_coordinates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _start(root, 1)
            publish_attempt_outcome(
                root,
                shard_index=7,
                slot_index=3,
                attempt_ordinal=1,
                outcome_class="retryable_http",
                http_status=503,
            )
            second = _start(root, 2)
            publish_call_checkpoint(root, _call_payload(second, attempt=2))

            copies = (
                (
                    attempt_start_path(root, 7, 3, 1),
                    attempt_start_path(root, 8, 4, 1),
                    lambda: load_attempt_start(root, 8, 4, 1),
                ),
                (
                    attempt_outcome_path(root, 7, 3, 1),
                    attempt_outcome_path(root, 8, 4, 1),
                    lambda: load_attempt_outcome(root, 8, 4, 1),
                ),
                (
                    call_checkpoint_path(root, 7, 3),
                    call_checkpoint_path(root, 8, 4),
                    lambda: load_call_checkpoint(root, 8, 4),
                ),
            )
            for source, destination, loader in copies:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
                os.chmod(destination, 0o600)
                with self.assertRaisesRegex(PilotCheckpointV3Error, "coordinates"):
                    loader()

    def test_secret_values_and_noncanonical_dsl_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = _start(root, 1)
            secret = "never-persist-this-secret"
            payload = _call_payload(start, attempt=1)
            payload["response"]["candidate_expression"] = " (var x1) "
            with self.assertRaisesRegex(PilotCheckpointV3Error, "canonical"):
                publish_call_checkpoint(root, payload)
            payload = _call_payload(start, attempt=1)
            payload["response"]["provider_model"] = secret
            with self.assertRaisesRegex(Exception, "secret"):
                publish_call_checkpoint(root, payload, forbidden_values=(secret,))
            self.assertFalse(call_checkpoint_path(root, 7, 3).exists())

            payload = _call_payload(start, attempt=1)
            payload["response"]["candidate_format"] = "invalid_json"
            with self.assertRaisesRegex(PilotCheckpointV3Error, "canonical DSL"):
                publish_call_checkpoint(root, payload)

    def test_nested_payload_mutation_after_detach_cannot_corrupt_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = _start(root, 1)
            payload = _call_payload(start, attempt=1)
            from src import pilot_checkpoint_v3 as module

            real_validate = module._validate_call_payload

            def mutate_original_after_detach(detached):
                payload["response"]["candidate_expression"] = "not valid dsl"
                return real_validate(detached)

            with mock.patch.object(
                module,
                "_validate_call_payload",
                side_effect=mutate_original_after_detach,
            ):
                publish_call_checkpoint(root, payload)
            loaded = load_call_checkpoint(root, 7, 3)
            self.assertEqual(
                loaded["payload"]["response"]["candidate_expression"],
                "(var x1)",
            )

    def test_shard_lock_rejects_concurrent_owner_and_recovers_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with acquire_shard_lock(root, 7):
                with self.assertRaisesRegex(PilotCheckpointV3Error, "already owned"):
                    with acquire_shard_lock(root, 7):
                        pass
            with acquire_shard_lock(root, 7):
                pass


if __name__ == "__main__":
    unittest.main()
