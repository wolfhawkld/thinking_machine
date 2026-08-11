from __future__ import annotations

from contextlib import nullcontext
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from src.pilot_checkpoint import sha256_json
from src.v3_finalizer import (
    V3FinalizationError,
    _CheckpointReplayGenerator,
    _aggregates,
    _assert_public_snapshot_safe,
    _classification,
    _construct_diagnostics,
    _paired_statistical_analysis,
    _private_endpoint,
    _resource_sensitivity,
    finalize_v3_campaign,
)
from src.verifier import Verifier


HASH = "a" * 64


def _entry(index: int, *, model: str = "m0", arm: str = "C") -> dict:
    return {
        "shard_index": index,
        "phase": "main",
        "plan_entry_sha256": HASH,
        "run_id": HASH,
        "route_binding_sha256": HASH,
        "world_index": index // 8,
        "world_seed": 2001 + index // 8,
        "depth": 3,
        "world_hash": HASH,
        "model_stratum": model,
        "arm_id": arm,
        "sampling_base_seed": 1729,
    }


def _manifest(entries: list[dict]) -> dict:
    payload = {
        "experiment": "v3-test",
        "execution_plan": entries,
        "frozen_config": {
            "episode": {"max_counterexamples_per_round": 2},
            "model_strata": [
                {"stratum_id": "m0"},
                {"stratum_id": "m1"},
            ],
        },
    }
    return {"payload": payload, "payload_sha256": sha256_json(payload)}


def _result(*, all_invalid: bool = False, selected: bool = True):
    final = None if not selected else SimpleNamespace(candidate=("var", "x1"))
    return SimpleNamespace(rounds=[], final_candidate=final)


def _metrics(*, all_invalid: bool = False) -> dict:
    return {
        "all_invalid": all_invalid,
        "search_valid_count": 0 if all_invalid else 20,
        "canonical_unique_count": 0 if all_invalid else 1,
        "behavioral_unique_count": 0 if all_invalid else 1,
        "outer_schema_valid_count": 20,
        "selected_probe_score": None if all_invalid else 1.0,
    }


def _seal_payload() -> dict:
    return {
        "execution_audit": {
            "physical_request_starts": 20,
            "retry_count": 0,
            "accepted_known_input_tokens": 20,
            "accepted_known_output_tokens": 20,
            "accepted_known_latency_ms": 20.0,
            "discarded_known_response_count": 0,
            "discarded_known_input_tokens": 0,
            "discarded_known_output_tokens": 0,
            "discarded_known_latency_ms": 0.0,
            "usage_unknown_start_marker_count": 0,
            "unresolved_slot_count": 0,
            "exhausted_slot_count": 0,
            "fatal_slot_count": 0,
            "ready_for_retry_slot_count": 0,
            "gross_known_token_lower_bound": 40,
            "gross_known_latency_ms": 20.0,
            "gross_usage_complete": True,
            "recovery_allows_actual_token_matched_claim": True,
        }
    }


def _complete_runs(
    *,
    m0_difference: int = 1,
    m1_difference: int = 2,
) -> list[dict]:
    differences = {"m0": m0_difference, "m1": m1_difference}
    runs = []
    for world in range(12):
        for model in ("m0", "m1"):
            for arm in ("L", "H", "C", "E2"):
                correct = 32 + (differences[model] if arm == "E2" else 0)
                canonical = 2 if arm == "H" else 1
                resource_tokens = 41 if arm == "E2" else 40
                runs.append(
                    {
                        "shard_index": len(runs) + 8,
                        "world_index": world,
                        "world_seed": 2001 + world,
                        "depth": 3 + world % 3,
                        "model_stratum": model,
                        "arm_id": arm,
                        "search_valid_count": 20,
                        "canonical_unique_count": canonical,
                        "behavioral_unique_count": canonical,
                        "outer_schema_valid_count": 20,
                        "failure_counts_by_code": {},
                        "outcome_status": "evaluated",
                        "observed_accuracy": correct / 64,
                        "primary_correct": correct,
                        "primary_denominator": 64,
                        "primary_score": correct / 64,
                        "world_solved": False,
                        "zero_is_observed_accuracy": True,
                        "resource": {
                            "physical_request_starts": 20,
                            "retry_count": 0,
                            "accepted_known_input_tokens": resource_tokens * 20,
                            "accepted_known_output_tokens": 0,
                            "accepted_known_latency_ms": 20.0,
                            "discarded_known_response_count": 0,
                            "discarded_known_input_tokens": 0,
                            "discarded_known_output_tokens": 0,
                            "discarded_known_latency_ms": 0.0,
                            "usage_unknown_start_marker_count": 0,
                            "unresolved_slot_count": 0,
                            "exhausted_slot_count": 0,
                            "fatal_slot_count": 0,
                            "ready_for_retry_slot_count": 0,
                            "gross_known_token_lower_bound": resource_tokens * 20,
                            "gross_known_latency_ms": 20.0,
                            "gross_usage_complete": True,
                            "recovery_allows_actual_token_matched_claim": True,
                        },
                    }
                )
    return runs


class _World:
    world_hash = HASH
    test = (None,) * 64


class V3PrivateEndpointTests(unittest.TestCase):
    def _endpoint(self, result, metrics, evaluator):
        return _private_endpoint(
            result,
            metrics,
            verifier=Verifier(),
            world=_World(),
            entry=_entry(0),
            test_evaluator=evaluator,
        )

    def test_all_invalid_is_null_observed_zero_primary_without_test_call(self) -> None:
        calls = 0

        def evaluator(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("must not be called")

        endpoint, invoked = self._endpoint(
            _result(all_invalid=True, selected=False),
            _metrics(all_invalid=True),
            evaluator,
        )
        self.assertFalse(invoked)
        self.assertEqual(calls, 0)
        self.assertIsNone(endpoint["observed_accuracy"])
        self.assertEqual(endpoint["primary_correct"], 0)
        self.assertEqual(endpoint["primary_score"], 0.0)
        self.assertFalse(endpoint["zero_is_observed_accuracy"])

    def test_runtime_failure_is_null_observed_zero_primary(self) -> None:
        endpoint, invoked = self._endpoint(
            _result(),
            _metrics(),
            lambda _result: {"runtime_valid": False},
        )
        self.assertTrue(invoked)
        self.assertEqual(endpoint["outcome_status"], "test_runtime_failure")
        self.assertIsNone(endpoint["observed_accuracy"])
        self.assertEqual(endpoint["primary_correct"], 0)

    def test_evaluator_exception_is_engineering_failure_not_scientific_zero(self) -> None:
        def broken(_result):
            raise RuntimeError("infrastructure broke")

        with self.assertRaisesRegex(V3FinalizationError, "evaluator failed"):
            self._endpoint(_result(), _metrics(), broken)

    def test_normal_endpoint_requires_integer_correct_out_of_64(self) -> None:
        endpoint, invoked = self._endpoint(
            _result(),
            _metrics(),
            lambda _result: {
                "runtime_valid": True,
                "correct": 37,
                "total": 64,
                "score": 37 / 64,
            },
        )
        self.assertTrue(invoked)
        self.assertEqual(endpoint["primary_correct"], 37)
        self.assertEqual(endpoint["primary_denominator"], 64)
        self.assertEqual(endpoint["observed_accuracy"], 37 / 64)
        with self.assertRaisesRegex(V3FinalizationError, "correct/64"):
            self._endpoint(
                _result(),
                _metrics(),
                lambda _result: {
                    "runtime_valid": True,
                    "correct": 37,
                    "total": 64,
                    "score": 0.5,
                },
            )

    def test_unexpected_no_selection_is_engineering_failure(self) -> None:
        with self.assertRaisesRegex(V3FinalizationError, "selection"):
            self._endpoint(
                _result(selected=False),
                _metrics(all_invalid=False),
                lambda _result: {"correct": 0, "total": 64, "score": 0.0},
            )


class V3ReplayGeneratorTests(unittest.TestCase):
    def _checkpoint(self, slot: int, prompt: str) -> dict:
        import hashlib

        return {
            "slot_index": slot,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "response": {
                "candidate_expression": "(var x1)",
                "input_tokens": 1,
                "output_tokens": 1,
                "latency_ms": 1.0,
                "accepted_provider_request_count": 1,
                "seed_supported": False,
                "provider_model": "snapshot",
                "finish_reason": "stop",
                "prompt_cache_hit_tokens": None,
                "prompt_cache_miss_tokens": None,
                "reasoning_tokens": 0,
                "candidate_format": "json_expression",
            },
        }

    def test_replay_is_exactly_ordered_and_prompt_bound(self) -> None:
        checkpoints = [self._checkpoint(index, f"p{index}") for index in range(20)]
        replay = _CheckpointReplayGenerator(checkpoints)
        first = replay.generate(
            "p0", temperature=0.2, round_index=0, candidate_index=0
        )
        self.assertEqual(first.expression, "(var x1)")
        with self.assertRaisesRegex(V3FinalizationError, "prompt"):
            replay.generate(
                "changed", temperature=0.2, round_index=0, candidate_index=1
            )


class V3FrozenAnalysisTests(unittest.TestCase):
    def _classification_aggregates(self, left: int, right: int) -> list[dict]:
        values = []
        for model, difference in (("m0", left), ("m1", right)):
            for arm, correct in (("C", 384), ("E2", 384 + difference)):
                values.append(
                    {
                        "model_stratum": model,
                        "arm_id": arm,
                        "episode_count": 12,
                        "primary_correct": correct,
                        "primary_denominator": 768,
                        "mean_primary_score": correct / 768,
                    }
                )
        return values

    def test_exact_classification_uses_integer_77_of_1536_threshold(self) -> None:
        below = _classification(
            self._classification_aggregates(38, 38), test_only=False
        )
        self.assertEqual(below["decision"], "mixed_or_small_development_signal")
        at_or_above = _classification(
            self._classification_aggregates(38, 39), test_only=False
        )
        self.assertEqual(
            at_or_above["decision"], "two_route_development_promising"
        )
        self.assertEqual(
            at_or_above["equal_stratum_delta_exact"],
            {"difference_correct": 77, "denominator": 1536},
        )
        nonpositive = _classification(
            self._classification_aggregates(0, -1), test_only=False
        )
        self.assertEqual(
            nonpositive["decision"],
            "two_route_nonpositive_development_signal",
        )

    def test_paired_sap_preserves_routes_worlds_and_depth_clusters(self) -> None:
        analysis = _paired_statistical_analysis(
            _complete_runs(),
            ("m0", "m1"),
            bootstrap_replicates=256,
            bootstrap_seed=20260809,
        )
        self.assertEqual(
            analysis["route_paired_contrasts"]["m0"]["mean_delta"],
            1 / 64,
        )
        self.assertEqual(
            analysis["route_paired_contrasts"]["m1"]["mean_delta"],
            2 / 64,
        )
        self.assertEqual(analysis["equal_stratum"]["mean_delta"], 3 / 128)
        self.assertEqual(
            analysis["bootstrap"]["route_intervals"]["m0"],
            {"lower": 1 / 64, "upper": 1 / 64},
        )
        self.assertEqual(
            analysis["sign_flip"]["route_p_values"]["m0"],
            2 / 4096,
        )
        self.assertEqual(len(analysis["per_depth_estimates"]), 3)

    def test_construct_and_resource_diagnostics_do_not_change_classification(self) -> None:
        runs = _complete_runs(m0_difference=-1, m1_difference=-1)
        aggregates = _aggregates(runs)
        classification = _classification(aggregates, test_only=False)
        construct = _construct_diagnostics(
            runs, ("m0", "m1"), test_only=False
        )
        resources = _resource_sensitivity(
            runs,
            ("m0", "m1"),
            classification,
            test_only=False,
        )
        self.assertFalse(construct["construct_validity_warning"])
        self.assertFalse(construct["manipulation_indeterminate"])
        self.assertEqual(
            classification["decision"],
            "two_route_nonpositive_development_signal",
        )
        self.assertTrue(resources["sensitivity_required"])
        self.assertTrue(
            all(
                route["pareto_status"]
                == "E2_accuracy_resource_dominated_or_tied"
                for route in resources["routes"]
            )
        )

    def test_exact_two_percent_token_range_remains_within_threshold(self) -> None:
        runs = _complete_runs()
        for model in ("m0", "m1"):
            for arm in ("L", "H", "C", "E2"):
                matching = [
                    run
                    for run in runs
                    if run["model_stratum"] == model and run["arm_id"] == arm
                ]
                total = 5_100 if arm == "H" else 5_000
                quotient, remainder = divmod(total, len(matching))
                for index, run in enumerate(matching):
                    run["resource"]["gross_known_token_lower_bound"] = (
                        quotient + (1 if index < remainder else 0)
                    )
        classification = _classification(_aggregates(runs), test_only=False)
        resources = _resource_sensitivity(
            runs,
            ("m0", "m1"),
            classification,
            test_only=False,
        )
        self.assertTrue(
            resources["actual_token_matched_claim_allowed_for_both_routes"]
        )
        self.assertFalse(resources["sensitivity_required"])
        for route in resources["routes"]:
            self.assertEqual(route["realized_token_relative_range"], 0.02)

    def test_unrelated_arm_retry_does_not_hide_E2_C_pareto(self) -> None:
        runs = _complete_runs()
        for model in ("m0", "m1"):
            l_run = next(
                run
                for run in runs
                if run["model_stratum"] == model and run["arm_id"] == "L"
            )
            l_run["resource"]["retry_count"] = 1
            l_run["resource"][
                "recovery_allows_actual_token_matched_claim"
            ] = False
        classification = _classification(_aggregates(runs), test_only=False)
        resources = _resource_sensitivity(
            runs,
            ("m0", "m1"),
            classification,
            test_only=False,
        )
        self.assertFalse(
            resources["actual_token_matched_claim_allowed_for_both_routes"]
        )
        for route in resources["routes"]:
            self.assertFalse(route["recovery_clean"])
            self.assertTrue(route["E2_C_recovery_clean"])
            self.assertEqual(
                route["pareto_status"],
                "accuracy_resource_tradeoff_unresolved",
            )


class V3TwoPassBarrierTests(unittest.TestCase):
    def _patch_campaign(self, manifest: dict, barrier_side_effect=None):
        barrier = {"payload": {"complete": True}}
        return (
            mock.patch("src.v3_campaign.acquire_campaign_lock", return_value=nullcontext()),
            mock.patch("src.v3_campaign.load_campaign_manifest", return_value=manifest),
            mock.patch("src.v3_campaign.validate_campaign_inventory"),
            mock.patch(
                "src.v3_campaign.load_generation_barrier",
                side_effect=barrier_side_effect,
                return_value=barrier,
            ),
        )

    def test_production_rejects_hook_and_noncampaign_output_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "injected"):
            finalize_v3_campaign(
                "campaign",
                test_evaluator=lambda *_args: None,
            )
        with self.assertRaisesRegex(ValueError, "fixed"):
            finalize_v3_campaign(
                "campaign",
                output_path="elsewhere.json",
            )

    def test_95_of_96_committed_episodes_fails_before_private_test(self) -> None:
        entries = []
        for world in range(12):
            for model in ("m0", "m1"):
                for arm in ("L", "H", "C", "E2"):
                    entry = _entry(len(entries), model=model, arm=arm)
                    entry["depth"] = 3 + world % 3
                    entries.append(entry)
        manifest = _manifest(entries)
        patches = self._patch_campaign(manifest)
        replay_count = 0

        def replay(*_args, **_kwargs):
            nonlocal replay_count
            replay_count += 1
            if replay_count == 96:
                raise V3FinalizationError("missing episode seal 103")
            return _result(), _metrics(), _seal_payload()

        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            mock.patch("src.v3_finalizer._replay_episode", side_effect=replay),
            mock.patch("src.v3_finalizer._private_endpoint") as endpoint,
        ):
            with self.assertRaisesRegex(V3FinalizationError, "missing episode seal"):
                finalize_v3_campaign("ignored")
        self.assertEqual(replay_count, 96)
        self.assertEqual(endpoint.call_count, 0)

    def test_late_barrier_tamper_after_all_replays_still_has_zero_test_calls(self) -> None:
        entries = []
        for world in range(12):
            for model in ("m0", "m1"):
                for arm in ("L", "H", "C", "E2"):
                    entries.append(_entry(len(entries), model=model, arm=arm))
        manifest = _manifest(entries)
        patches = self._patch_campaign(
            manifest, barrier_side_effect=RuntimeError("late tamper")
        )
        with (
            patches[0],
            patches[1],
            patches[2],
            patches[3],
            mock.patch(
                "src.v3_finalizer._replay_episode",
                return_value=(_result(), _metrics(), _seal_payload()),
            ) as replay,
            mock.patch("src.v3_finalizer._private_endpoint") as endpoint,
        ):
            with self.assertRaisesRegex(RuntimeError, "late tamper"):
                finalize_v3_campaign("ignored")
        self.assertEqual(replay.call_count, 96)
        self.assertEqual(endpoint.call_count, 0)

    def test_complete_production_snapshot_runs_the_frozen_analysis_and_is_unique(self) -> None:
        entries = []
        for world in range(12):
            for model in ("m0", "m1"):
                for arm in ("L", "H", "C", "E2"):
                    entry = _entry(len(entries), model=model, arm=arm)
                    entry["depth"] = 3 + world % 3
                    entries.append(entry)
        manifest = _manifest(entries)
        patches = self._patch_campaign(manifest)

        def replay(*_args, **kwargs):
            entry = kwargs["entry"]
            metrics = _metrics()
            if entry["arm_id"] == "H":
                metrics["canonical_unique_count"] = 2
                metrics["behavioral_unique_count"] = 2
            metrics.update(
                {
                    "round_best_scores": [0.5] * 5,
                    "failure_counts": {"by_code": {}},
                    "candidate_format_counts": {"json_expression": 20},
                    "temperature_trajectory": [0.2] * 5,
                    "controller_trace": [],
                }
            )
            return _result(), metrics, _seal_payload()

        def endpoint(*_args, **kwargs):
            correct = 36 if kwargs["entry"]["arm_id"] == "E2" else 32
            return (
                {
                    "outcome_status": "evaluated",
                    "observed_accuracy": correct / 64,
                    "primary_correct": correct,
                    "primary_denominator": 64,
                    "primary_score": correct / 64,
                    "world_solved": False,
                    "zero_is_observed_accuracy": True,
                },
                True,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "v3-finalized-snapshot.json"
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                mock.patch("src.v3_finalizer._replay_episode", side_effect=replay),
                mock.patch("src.v3_finalizer.generate_world", return_value=_World()),
                mock.patch("src.v3_finalizer._private_endpoint", side_effect=endpoint),
            ):
                snapshot = finalize_v3_campaign(root)
            self.assertTrue(snapshot["evidence"])
            self.assertEqual(
                snapshot["classification"]["decision"],
                "two_route_development_promising",
            )
            self.assertTrue(snapshot["statistical_analysis"]["eligible"])
            self.assertEqual(
                snapshot["statistical_analysis"]["bootstrap"]["replicates"],
                100000,
            )
            self.assertFalse(
                snapshot["construct_diagnostics"]["manipulation_indeterminate"]
            )
            self.assertTrue(
                snapshot["resource_sensitivity"][
                    "actual_token_matched_claim_allowed_for_both_routes"
                ]
            )
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                finalize_v3_campaign(root)

    def test_reduced_test_fixture_finalizes_and_publishes_exclusive_0600(self) -> None:
        entry = _entry(0)
        manifest = _manifest([entry])
        patches = self._patch_campaign(manifest)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "snapshot.json"
            with (
                patches[0],
                patches[1],
                patches[2],
                patches[3],
                mock.patch(
                    "src.v3_finalizer._replay_episode",
                    return_value=(_result(), _metrics(), _seal_payload()),
                ),
                mock.patch("src.v3_finalizer.generate_world", return_value=_World()),
            ):
                snapshot = finalize_v3_campaign(
                    "ignored",
                    output_path=output,
                    expected_main_entries=1,
                    test_evaluator=lambda _result: {
                        "runtime_valid": True,
                        "correct": 32,
                        "total": 64,
                        "score": 0.5,
                    },
                )
            self.assertEqual(snapshot["runs"][0]["primary_correct"], 32)
            self.assertEqual(snapshot["runs"][0]["observed_accuracy"], 0.5)
            self.assertFalse(snapshot["evidence"])
            self.assertFalse(snapshot["classification"]["eligible"])
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                finalize_v3_campaign(
                    "ignored",
                    output_path=output,
                    expected_main_entries=1,
                    test_evaluator=lambda *_args: None,
                )

    def test_public_snapshot_rejects_candidate_and_private_fields(self) -> None:
        for unsafe in (
            {"candidate_expression": "(var x1)"},
            {"expression": "(var x1)"},
            {"canonical_hash": HASH},
            {"candidate_digest_hash": HASH},
            {"predictions": [1]},
            {"test_labels": [1]},
            {"law": "hidden"},
            {"raw": "provider text"},
            {"raw_endpoint": "https://secret.invalid"},
            {"api_key": "secret"},
            {"key": "secret"},
        ):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(V3FinalizationError):
                    _assert_public_snapshot_safe(unsafe)


if __name__ == "__main__":
    unittest.main()
