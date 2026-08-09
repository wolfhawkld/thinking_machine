from __future__ import annotations

import copy
import io
import json
import math
import os
from pathlib import Path
import tempfile
import unittest

from src.credentials import ProviderCredentials
from src.development_pilot import (
    DEVELOPMENT_PILOT_MODEL,
    DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
    DEVELOPMENT_PILOT_VOLCENGINE_RESPONSE_MODEL,
)
from src.oracle_diagnostics import (
    OracleDiagnosticError,
    REPLAY_CRITICAL_FILES,
    _boolean_rate,
    _kendall_tau_b,
    _pearson,
    _source_file_hashes,
    _spearman,
    analyze_oracle_diagnostic,
    main,
)
from src.provenance import PROJECT_ROOT, source_manifest
from src.runner import GenerationResponse
from src.staged_pilot import finalize_snapshot, run_stage


class _FakeGenerator:
    def __init__(self, factory: "_FakeFactory") -> None:
        self.factory = factory

    def generate(self, prompt: str, **kwargs: object) -> GenerationResponse:
        del prompt, kwargs
        self.factory.calls += 1
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


class _FakeFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, context: object) -> _FakeGenerator:
        del context
        return _FakeGenerator(self)


def _credentials() -> ProviderCredentials:
    return ProviderCredentials(
        base_url=DEVELOPMENT_PILOT_VOLCENGINE_ENDPOINT,
        model=DEVELOPMENT_PILOT_MODEL,
        api_key="oracle-diagnostic-test-secret",
    )


class CorrelationHelperTests(unittest.TestCase):
    def test_pearson_spearman_and_kendall_handle_ties(self) -> None:
        self.assertAlmostEqual(_pearson([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertAlmostEqual(
            _spearman([1, 2, 2, 3], [1, 3, 2, 4]),
            3 / math.sqrt(10),
        )
        self.assertAlmostEqual(
            _kendall_tau_b([1, 1, 2], [1, 2, 3]),
            2 / (6**0.5),
        )
        self.assertIsNone(_pearson([1, 1], [1, 2]))
        self.assertIsNone(_spearman([1], [1]))
        self.assertIsNone(_kendall_tau_b([1, 1], [2, 2]))

    def test_boolean_rate_preserves_an_empty_denominator(self) -> None:
        self.assertEqual(
            _boolean_rate([None, None]),
            {"true_count": 0, "defined_run_count": 0, "rate": None},
        )
        self.assertEqual(
            _boolean_rate([True, False, None]),
            {"true_count": 1, "defined_run_count": 2, "rate": 0.5},
        )

    def test_replay_critical_source_hash_drift_is_rejected(self) -> None:
        provenance = source_manifest(PROJECT_ROOT)
        for relative_path in REPLAY_CRITICAL_FILES:
            with self.subTest(relative_path=relative_path):
                tampered = copy.deepcopy(provenance)
                entry = next(
                    item
                    for item in tampered["files"]
                    if item["path"] == relative_path
                )
                entry["sha256"] = "0" * 64
                with self.assertRaisesRegex(OracleDiagnosticError, "source drifted"):
                    _source_file_hashes({"source_manifest": tampered})


class OracleDiagnosticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls._temporary.name)
        cls.campaign = cls.root / "campaign"
        cls.snapshot_path = cls.root / "s3.json"
        cls.provenance = source_manifest(PROJECT_ROOT)
        cls.factory = _FakeFactory()
        for stage_index in range(3):
            run_stage(
                cls.campaign,
                _credentials(),
                generator_factory=cls.factory,
                provenance_manifest=cls.provenance,
                resume=stage_index > 0,
                progress_stream=io.StringIO(),
            )
        finalize_snapshot(
            cls.campaign,
            8,
            current_source_manifest=cls.provenance,
            output_path=cls.snapshot_path,
        )
        cls.calls_after_campaign = cls.factory.calls
        cls.output_path = cls.root / "diagnostic.json"
        main(
            [
                "--campaign-dir",
                str(cls.campaign),
                "--snapshot",
                str(cls.snapshot_path),
                "--output",
                str(cls.output_path),
            ]
        )
        cls.result = json.loads(cls.output_path.read_text())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_complete_campaign_is_replayed_and_diagnosed_offline(self) -> None:
        result = self.result

        self.assertEqual(self.factory.calls, self.calls_after_campaign)
        self.assertEqual(result["integrity"]["checkpoints_verified"], 56)
        self.assertEqual(result["integrity"]["world_seals_verified"], 8)
        self.assertEqual(result["integrity"]["snapshot_runs_exactly_reproduced"], 56)
        self.assertEqual(
            result["integrity"]["search_eligible_candidates_evaluated"], 1120
        )
        self.assertEqual(result["integrity"]["terminal_no_selection_runs"], 0)
        for arm in ("L", "M", "H", "A", "C", "MTX", "E"):
            summary = result["arm_summary"][arm]
            self.assertEqual(
                summary["mean_selected_terminal_zero_sensitivity"],
                summary["mean_oracle_at_20_terminal_zero_sensitivity"],
            )
            self.assertEqual(
                summary["mean_selection_regret_terminal_zero_sensitivity"], 0.0
            )
        encoded = json.dumps(result, sort_keys=True).lower()
        for forbidden in (
            "candidate_expression",
            "test_labels",
            "test_examples",
            "raw_prompt",
            "raw_response",
            "api_key",
            "authorization",
            "endpoint",
            "base_url",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_cli_exclusively_publishes_mode_0600(self) -> None:
        self.assertEqual(os.stat(self.output_path).st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            main(
                [
                    "--campaign-dir",
                    str(self.campaign),
                    "--snapshot",
                    str(self.snapshot_path),
                    "--output",
                    str(self.output_path),
                ]
            )

    def test_non_s3_snapshot_is_rejected_before_candidate_evaluation(self) -> None:
        snapshot = json.loads(self.snapshot_path.read_text())
        snapshot["stage"]["stage_id"] = "S2"
        malformed = self.root / "not-s3.json"
        malformed.write_text(json.dumps(snapshot), encoding="utf-8")
        with self.assertRaisesRegex(OracleDiagnosticError, "complete S3"):
            analyze_oracle_diagnostic(self.campaign, malformed)

    def test_selected_test_tamper_is_rejected(self) -> None:
        snapshot = json.loads(self.snapshot_path.read_text())
        snapshot["runs"][0]["final_test"]["accuracy"] = 0.123
        tampered = self.root / "tampered-s3.json"
        tampered.write_text(json.dumps(snapshot), encoding="utf-8")
        with self.assertRaisesRegex(OracleDiagnosticError, "disagrees with the S3"):
            analyze_oracle_diagnostic(self.campaign, tampered)


if __name__ == "__main__":
    unittest.main()
