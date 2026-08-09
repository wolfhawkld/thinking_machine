from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from src.pilot_checkpoint import (
    INVALID_CANDIDATE_SENTINEL,
    PilotCheckpointError,
    atomic_publish_json,
    checkpoint_path,
    load_envelope,
    load_shard_checkpoint,
    publish_envelope,
    publish_shard_checkpoint,
)


def _payload(expression: str = "(var x1)", *, syntax_valid: bool = True) -> dict:
    return {
        "shard_index": 0,
        "run": {
            "candidates": [
                {
                    "candidate_expression": expression,
                    "syntax_valid": syntax_valid,
                }
            ]
        },
    }


class PilotCheckpointTests(unittest.TestCase):
    def test_checkpoint_publication_is_exclusive_mode_0600_and_directory_fsynced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("src.pilot_checkpoint._fsync_directory") as fsync_dir:
                publish_shard_checkpoint(root, _payload())
            path = checkpoint_path(root, 0)
            loaded = load_shard_checkpoint(root, 0)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(loaded["payload"]["shard_index"], 0)
            self.assertGreaterEqual(fsync_dir.call_count, 1)
            self.assertEqual(list(path.parent.glob(".*.tmp-*")), [])
            with self.assertRaises(FileExistsError):
                publish_shard_checkpoint(root, _payload())

    def test_checkpoint_accepts_only_canonical_dsl_or_fixed_invalid_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish_shard_checkpoint(root, _payload())
            publish_shard_checkpoint(
                root,
                {**_payload(INVALID_CANDIDATE_SENTINEL, syntax_valid=False), "shard_index": 1},
            )
            with self.assertRaisesRegex(PilotCheckpointError, "canonical"):
                publish_shard_checkpoint(
                    root,
                    {**_payload(" (var x1) "), "shard_index": 2},
                )
            with self.assertRaisesRegex(PilotCheckpointError, "syntax_valid=false"):
                publish_shard_checkpoint(
                    root,
                    {**_payload(INVALID_CANDIDATE_SENTINEL), "shard_index": 3},
                )

    def test_raw_private_and_secret_values_are_never_persisted(self) -> None:
        secret = "checkpoint-secret-value"
        raw_endpoint = "https://raw-provider.example/v3"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field in ("prompt", "raw_response", "private_test", "test_label"):
                with self.subTest(field=field):
                    payload = _payload()
                    payload[field] = "unsafe"
                    with self.assertRaises(PilotCheckpointError):
                        publish_shard_checkpoint(root, payload)
            with self.assertRaises(PilotCheckpointError):
                publish_shard_checkpoint(
                    root,
                    {**_payload(), "accidental": secret},
                    forbidden_values=(secret,),
                )
            with self.assertRaises(PilotCheckpointError):
                publish_shard_checkpoint(
                    root,
                    {**_payload(), "provider_url": raw_endpoint},
                    forbidden_values=(raw_endpoint,),
                )
            self.assertFalse(checkpoint_path(root, 0).exists())

    def test_tamper_and_legacy_attempt_import_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish_shard_checkpoint(root, _payload())
            path = checkpoint_path(root, 0)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["payload"]["run"]["candidates"][0]["syntax_valid"] = False
            path.write_text(json.dumps(value), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(PilotCheckpointError, "hash mismatch"):
                load_shard_checkpoint(root, 0)

            legacy = root / "legacy.json"
            publish_envelope(
                legacy,
                kind="attempt-ledger",
                payload={"successful_calls": 234},
            )
            with self.assertRaisesRegex(PilotCheckpointError, "cannot be imported"):
                load_envelope(
                    legacy,
                    expected_kind="staged-pilot-shard-checkpoint",
                )

    def test_unhashed_top_level_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            publish_shard_checkpoint(root, _payload())
            path = checkpoint_path(root, 0)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["raw_prompt"] = "unhashed-secret"
            path.write_text(json.dumps(value), encoding="utf-8")
            os.chmod(path, 0o600)
            with self.assertRaisesRegex(PilotCheckpointError, "keys drifted"):
                load_shard_checkpoint(root, 0)

    def test_failed_publication_leaves_neither_commit_nor_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            with (
                mock.patch("src.pilot_checkpoint.os.link", side_effect=OSError("crash")),
                self.assertRaises(OSError),
            ):
                atomic_publish_json(path, {"safe": True})
            self.assertFalse(path.exists())
            self.assertEqual(list(path.parent.glob(".*.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
