from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from src import spark_strong_k4_benchmark as benchmark
from src import spark_strong_k4_formal as formal
from src import spark_strong_k4_posthoc as posthoc
from src.provenance import PROJECT_ROOT, source_manifest
from src.providers.openai_compatible import OpenAICompatibleGenerator


SEALED_DIR = (
    PROJECT_ROOT / "artifacts" / "spark-strong-k4-fair-choice-formal-20260824"
)
SEALED_HASHES = {
    "plan": "98476cd5bb77bf015dfb75b8caad25763042e2c7b47a1dc2fa7884c400a81d7b",
    "public": "9084061dffa75f2b013c2d78af036bba5f444ee130315457c35a74b4aad6314e",
    "bundle": "8d386109efcebb9a71185c6162aa8599b3f88f691727e5d838ed66234eb2edd9",
    "private": "d5b6961ac911a2f90d979c3de12d2e51847fdca9e087752385a7e8895b3ae9bb",
    "analysis": "89d6edae10314ccda36ea0d50d5b0acceb648adc6163680eccaaa17074605b31",
}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    return _sha256_bytes(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )


def _build_kwargs() -> dict[str, object]:
    return {
        "plan_path": SEALED_DIR / "formal-plan.json",
        "expected_plan_file_sha256": SEALED_HASHES["plan"],
        "public_manifest_path": SEALED_DIR / "public.json",
        "expected_public_manifest_file_sha256": SEALED_HASHES["public"],
        "generation_bundle_path": SEALED_DIR / "generation-bundle.json",
        "expected_generation_bundle_file_sha256": SEALED_HASHES["bundle"],
        "private_key_path": SEALED_DIR / "private.json",
        "expected_private_key_file_sha256": SEALED_HASHES["private"],
        "formal_analysis_path": SEALED_DIR / "analysis.json",
        "expected_formal_analysis_file_sha256": SEALED_HASHES["analysis"],
    }


def _input_bytes() -> dict[str, bytes]:
    return {
        "plan": (SEALED_DIR / "formal-plan.json").read_bytes(),
        "public": (SEALED_DIR / "public.json").read_bytes(),
        "bundle": (SEALED_DIR / "generation-bundle.json").read_bytes(),
        "private": (SEALED_DIR / "private.json").read_bytes(),
        "analysis": (SEALED_DIR / "analysis.json").read_bytes(),
    }


class FairChoicePosthocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.artifact = posthoc.build_fair_choice_posthoc_diagnostic(
            **_build_kwargs()
        )

    def test_real_sealed_build_is_offline_and_preserves_formal_result(self) -> None:
        before = _input_bytes()
        forbidden = AssertionError("formal inference or provider path was entered")
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(
                    benchmark,
                    "score_model_responses",
                    side_effect=forbidden,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    benchmark,
                    "classify_joint_routes",
                    side_effect=forbidden,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    formal,
                    "analyze_fair_choice_science",
                    side_effect=forbidden,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    formal,
                    "run_fair_choice_science",
                    side_effect=forbidden,
                )
            )
            stack.enter_context(
                mock.patch.object(
                    OpenAICompatibleGenerator,
                    "generate",
                    side_effect=forbidden,
                )
            )
            artifact = posthoc.build_fair_choice_posthoc_diagnostic(
                **_build_kwargs()
            )
        self.assertEqual(_input_bytes(), before)
        self.assertEqual(artifact["status"], "post_hoc_not_confirmatory")
        self.assertTrue(artifact["post_hoc_explanatory_only"])
        self.assertFalse(artifact["evidence"])
        self.assertTrue(artifact["designed_after_formal_outcomes_opened"])
        self.assertEqual(artifact["provider_calls_made"], 0)
        self.assertFalse(artifact["formal_analysis_mutated"])
        self.assertFalse(artifact["formal_analysis_recomputed"])
        self.assertFalse(artifact["new_p_values_computed"])
        self.assertFalse(artifact["new_classification_labels_created"])
        self.assertEqual(
            artifact["interpretation_limit"], posthoc.INTERPRETATION_LIMIT
        )

        analysis = json.loads(before["analysis"])
        reference = artifact["formal_result_reference"]
        self.assertEqual(
            reference["joint_classification"],
            analysis["joint_result"]["joint_classification"],
        )
        self.assertEqual(
            reference["route_classifications"],
            {
                route_id: row["classification"]
                for route_id, row in analysis["joint_result"][
                    "route_classifications"
                ].items()
            },
        )
        self.assertFalse(reference["classification_recomputed"])
        self.assertFalse(reference["classification_modified"])
        self.assertEqual(
            artifact["historical_formal_source_manifest_sha256"],
            json.loads(before["plan"])["current_source_manifest_sha256"],
        )
        self.assertEqual(
            artifact["diagnostic_source_manifest_sha256"],
            source_manifest(PROJECT_ROOT)["source_manifest_sha256"],
        )
        for name, expected in SEALED_HASHES.items():
            binding_name = {
                "plan": "formal_plan",
                "public": "public_manifest",
                "bundle": "generation_bundle",
                "private": "private_key",
                "analysis": "formal_analysis",
            }[name]
            self.assertEqual(
                artifact["input_bindings"][binding_name]["file_sha256"],
                expected,
            )

    def test_frozen_opportunities_baselines_and_concentrations(self) -> None:
        opportunity = self.artifact["opportunity_landscape"]
        arms = opportunity["benchmark_arms"]
        expected_totals = {
            "factual": {
                "K1": (179, 32),
                "K2": (91, 32),
                "K3": (91, 32),
                "K4_full_pool": (53, 32),
            },
            "sham": {
                "K1": (183, 32),
                "K2": (62, 17),
                "K3": (62, 17),
                "K4_full_pool": (22, 14),
            },
        }
        for arm, endpoints in expected_totals.items():
            for endpoint, (action_count, world_count) in endpoints.items():
                self.assertEqual(
                    arms[arm][endpoint]["qualifying_raw_action_count"],
                    action_count,
                )
                self.assertEqual(
                    arms[arm][endpoint]["world_with_opportunity_count"],
                    world_count,
                )
        self.assertEqual(
            opportunity["same_raw_frame_factual_sham_tables"],
            {
                "K1": {
                    "both": 171,
                    "factual_only": 8,
                    "sham_only": 12,
                    "neither": 129,
                    "frame_count": 320,
                },
                "K2": {
                    "both": 45,
                    "factual_only": 46,
                    "sham_only": 17,
                    "neither": 212,
                    "frame_count": 320,
                },
                "K3": {
                    "both": 45,
                    "factual_only": 46,
                    "sham_only": 17,
                    "neither": 212,
                    "frame_count": 320,
                },
                "K4_full_pool": {
                    "both": 16,
                    "factual_only": 37,
                    "sham_only": 6,
                    "neither": 261,
                    "frame_count": 320,
                },
            },
        )

        b_star_expected = {
            "deepseek-flash": (15, 10, 7, (7, 0, 12, 13)),
            "deepseek-pro": (6, 4, 3, (3, 1, 16, 12)),
            "glm-5.2": (12, 8, 4, (6, 1, 13, 12)),
        }
        for route_id in benchmark.CANONICAL_ROUTE_IDS:
            route = self.artifact["baseline_overlaps"]["routes"][route_id]
            self.assertEqual(route["policy_count"], 24)
            self.assertEqual(
                [row["policy_id"] for row in route["policies"]],
                list(benchmark.BASELINE_POLICY_IDS),
            )
            b_star = [row for row in route["policies"] if row["is_B_star"]]
            self.assertEqual(len(b_star), 1)
            b_star = b_star[0]
            factual, sham, both, table = b_star_expected[route_id]
            self.assertEqual(b_star["factual_action_match_count"], factual)
            self.assertEqual(b_star["sham_action_match_count"], sham)
            self.assertEqual(b_star["both_arms_action_match_count"], both)
            self.assertEqual(
                tuple(
                    b_star["factual_K4_overlap"][cell]["count"]
                    for cell in (
                        "model_and_baseline",
                        "model_only",
                        "baseline_only",
                        "neither",
                    )
                ),
                table,
            )

        concentration_expected = {
            "deepseek-flash": (7, 1, 7, 0),
            "deepseek-pro": (4, 2, 3, 1),
            "glm-5.2": (7, 2, 6, 1),
        }
        for route_id, expected in concentration_expected.items():
            route = self.artifact["strong_hit_concentration"]["routes"][
                route_id
            ]
            self.assertEqual(
                (
                    route["factual_K4_hit_count"],
                    route["unique_child_behavior_count"],
                    route["constant_child_hit_count"],
                    route["nonconstant_child_hit_count"],
                ),
                expected,
            )

        overlay_expected = {
            "deepseek-flash": {
                "factual": (7, 25, 0, 0),
                "sham": (3, 11, 18, 0),
            },
            "deepseek-pro": {
                "factual": (4, 28, 0, 0),
                "sham": (1, 13, 18, 0),
            },
            "glm-5.2": {
                "factual": (7, 25, 0, 0),
                "sham": (3, 11, 18, 0),
            },
        }
        for route_id, arm_rows in overlay_expected.items():
            for arm, expected in arm_rows.items():
                row = opportunity["route_selected_opportunity_overlay"][route_id][
                    arm
                ]["K4_full_pool"]
                self.assertEqual(
                    tuple(
                        row[key]
                        for key in (
                            "selected_hit",
                            "opportunity_miss",
                            "no_opportunity",
                            "invalid_choice",
                        )
                    ),
                    expected,
                )

    def test_frozen_raw_display_preferences_and_pair_same_choice(self) -> None:
        expected = {
            "deepseek-flash": {
                "same": 23,
                "factual": (
                    [13, 0, 0, 4, 1, 13, 0, 1, 0, 0],
                    [10, 1, 1, 2, 3, 3, 6, 4, 1, 1],
                ),
                "sham": (
                    [14, 1, 1, 3, 1, 11, 0, 1, 0, 0],
                    [13, 2, 0, 1, 5, 5, 4, 2, 0, 0],
                ),
            },
            "deepseek-pro": {
                "same": 22,
                "factual": (
                    [7, 1, 1, 3, 2, 8, 3, 2, 3, 2],
                    [16, 4, 2, 2, 4, 0, 1, 3, 0, 0],
                ),
                "sham": (
                    [6, 1, 1, 1, 3, 10, 4, 1, 4, 1],
                    [15, 3, 2, 3, 2, 2, 1, 3, 1, 0],
                ),
            },
            "glm-5.2": {
                "same": 18,
                "factual": (
                    [17, 0, 3, 1, 1, 5, 0, 1, 3, 1],
                    [5, 6, 5, 4, 3, 1, 4, 1, 1, 2],
                ),
                "sham": (
                    [15, 0, 3, 2, 3, 3, 2, 2, 2, 0],
                    [5, 4, 6, 3, 4, 3, 4, 2, 0, 1],
                ),
            },
        }
        routes = self.artifact["selection_preferences"]["routes"]
        for route_id, route_expected in expected.items():
            self.assertEqual(
                routes[route_id]["same_raw_action_pair_count"],
                route_expected["same"],
            )
            for arm in benchmark.ARMS:
                row = routes[route_id]["arms"][arm]
                raw, display = route_expected[arm]
                self.assertEqual(
                    [row["raw_action_index_counts"][str(index)] for index in range(10)],
                    raw,
                )
                self.assertEqual(
                    [row["display_position_counts"][str(index)] for index in range(10)],
                    display,
                )

    def test_per_pair_and_stratum_opportunities_reconstruct_global_counts(self) -> None:
        private_key = json.loads(
            (SEALED_DIR / "private.json").read_text(encoding="utf-8")
        )
        arms = self.artifact["opportunity_landscape"]["benchmark_arms"]
        for arm in benchmark.ARMS:
            for endpoint in benchmark.ENDPOINT_NAMES:
                expected_counts = [
                    sum(
                        action["endpoint_flags"][endpoint]
                        for action in pair["arms"][arm]["actions"]
                    )
                    for pair in private_key["pairs"]
                ]
                row = arms[arm][endpoint]
                self.assertEqual(
                    row["per_pair_qualifying_action_counts"], expected_counts
                )
                self.assertEqual(
                    set(row["by_construction_stratum"]), set(posthoc.STRATA)
                )
                for stratum in posthoc.STRATA:
                    stratum_counts = [
                        count
                        for pair, count in zip(
                            private_key["pairs"], expected_counts, strict=True
                        )
                        if pair["world_binding"]["construction_stratum"]
                        == stratum
                    ]
                    stratum_row = row["by_construction_stratum"][stratum]
                    self.assertEqual(stratum_row["pair_count"], 8)
                    self.assertEqual(
                        stratum_row["qualifying_raw_action_count"],
                        sum(stratum_counts),
                    )
                    self.assertEqual(
                        stratum_row["world_with_opportunity_count"],
                        sum(count > 0 for count in stratum_counts),
                    )
                    self.assertEqual(
                        stratum_row["per_world_opportunity_count_histogram"],
                        {
                            str(count): stratum_counts.count(count)
                            for count in range(11)
                        },
                    )

    def test_paired_selected_K2_decomposition_and_partitions(self) -> None:
        decomposition = self.artifact[
            "paired_selected_endpoint_decomposition"
        ]
        self.assertEqual(
            decomposition["interpretation"],
            posthoc.PAIRED_SELECTION_INTERPRETATION,
        )
        expected = {
            "deepseek-flash": {
                "factual_only": (6, 5, 1),
                "sham_only": (0, 0, 0),
            },
            "deepseek-pro": {
                "factual_only": (5, 4, 1),
                "sham_only": (2, 0, 2),
            },
            "glm-5.2": {
                "factual_only": (6, 2, 4),
                "sham_only": (2, 0, 2),
            },
        }
        for route_id, cells in expected.items():
            endpoint = decomposition["routes"][route_id]["K2"]
            for name, (total, same, different) in cells.items():
                self.assertEqual(endpoint[name]["count"], total)
                self.assertEqual(
                    endpoint[name]["same_raw_action"]["count"], same
                )
                self.assertEqual(
                    endpoint[name]["different_raw_action"]["count"],
                    different,
                )
        for route in decomposition["routes"].values():
            for endpoint in route.values():
                self.assertEqual(endpoint["invalid"]["count"], 0)
                self.assertEqual(
                    sum(
                        endpoint[name]["count"]
                        for name in (
                            "both_hit",
                            "factual_only",
                            "sham_only",
                            "neither",
                        )
                    ),
                    32,
                )

    def test_all_five_external_file_hashes_are_exact_allowlists(self) -> None:
        hash_fields = (
            "expected_plan_file_sha256",
            "expected_public_manifest_file_sha256",
            "expected_generation_bundle_file_sha256",
            "expected_private_key_file_sha256",
            "expected_formal_analysis_file_sha256",
        )
        for field in hash_fields:
            with self.subTest(field=field):
                kwargs = _build_kwargs()
                kwargs[field] = "0" * 64
                with self.assertRaises(posthoc.FairChoicePosthocError):
                    posthoc.build_fair_choice_posthoc_diagnostic(**kwargs)

    def test_private_key_is_read_only_after_complete_public_barrier(self) -> None:
        labels: list[str] = []
        original = posthoc._read_bound_json

        def recording_reader(*args: object, **kwargs: object):
            labels.append(str(kwargs["label"]))
            return original(*args, **kwargs)

        with mock.patch.object(
            posthoc, "_read_bound_json", side_effect=recording_reader
        ):
            posthoc.build_fair_choice_posthoc_diagnostic(**_build_kwargs())
        self.assertEqual(
            labels,
            [
                "formal plan",
                "public manifest",
                "generation bundle",
                "formal analysis",
                "private key",
            ],
        )

        malformed = json.loads(
            (SEALED_DIR / "generation-bundle.json").read_text(encoding="utf-8")
        )
        malformed["route_response_artifacts"].pop()
        unsigned = {
            key: value
            for key, value in malformed.items()
            if key != "generation_bundle_sha256"
        }
        malformed["generation_bundle_sha256"] = _sha256_json(unsigned)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incomplete-bundle.json"
            path.write_text(json.dumps(malformed), encoding="utf-8")
            kwargs = _build_kwargs()
            kwargs["generation_bundle_path"] = path
            kwargs["expected_generation_bundle_file_sha256"] = _sha256_bytes(
                path.read_bytes()
            )
            labels.clear()
            with mock.patch.object(
                posthoc, "_read_bound_json", side_effect=recording_reader
            ):
                with self.assertRaises(posthoc.FairChoicePosthocError):
                    posthoc.build_fair_choice_posthoc_diagnostic(**kwargs)
            self.assertNotIn("private key", labels)

    def test_closed_schema_and_self_digest_reject_tampering(self) -> None:
        stale_digest = copy.deepcopy(self.artifact)
        stale_digest["provider_calls_made"] = 1
        with self.assertRaises(posthoc.FairChoicePosthocError):
            posthoc.validate_fair_choice_posthoc_diagnostic(stale_digest)

        top_extra = copy.deepcopy(self.artifact)
        top_extra["unexpected"] = True
        unsigned = {
            key: value
            for key, value in top_extra.items()
            if key != "posthoc_diagnostic_sha256"
        }
        top_extra["posthoc_diagnostic_sha256"] = _sha256_json(unsigned)
        with self.assertRaises(posthoc.FairChoicePosthocError):
            posthoc.validate_fair_choice_posthoc_diagnostic(top_extra)

    def test_cli_writes_0600_once_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "posthoc.json"
            argv = [
                "diagnose",
                "--plan",
                str(SEALED_DIR / "formal-plan.json"),
                "--expected-plan-file-sha256",
                SEALED_HASHES["plan"],
                "--public",
                str(SEALED_DIR / "public.json"),
                "--expected-public-file-sha256",
                SEALED_HASHES["public"],
                "--bundle",
                str(SEALED_DIR / "generation-bundle.json"),
                "--expected-bundle-file-sha256",
                SEALED_HASHES["bundle"],
                "--private",
                str(SEALED_DIR / "private.json"),
                "--expected-private-file-sha256",
                SEALED_HASHES["private"],
                "--formal-analysis",
                str(SEALED_DIR / "analysis.json"),
                "--expected-formal-analysis-file-sha256",
                SEALED_HASHES["analysis"],
                "--output",
                str(output),
            ]
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(posthoc.main(argv), 0)
            receipt = json.loads(stdout.getvalue())
            self.assertEqual(receipt["file_sha256"], _sha256_bytes(output.read_bytes()))
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            written = json.loads(output.read_text(encoding="utf-8"))
            posthoc.validate_fair_choice_posthoc_diagnostic(written)
            before = output.read_bytes()
            with self.assertRaises(posthoc.FairChoicePosthocError):
                posthoc.main(argv)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
