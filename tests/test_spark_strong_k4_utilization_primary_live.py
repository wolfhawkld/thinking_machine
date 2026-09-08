from __future__ import annotations

from contextlib import redirect_stderr
import copy
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from src import spark_strong_k4_utilization_primary_live as live
from src.providers.openai_compatible import OpenAICompatibleGenerator, TransportError
from src.runner import GenerationResponse


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for child in value.values():
            keys.update(_all_keys(child))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for child in value:
            keys.update(_all_keys(child))
        return keys
    return set()


def _generator() -> OpenAICompatibleGenerator:
    """Construct the sealed route without injecting a transport or making I/O."""

    return OpenAICompatibleGenerator(
        base_url="https://api.deepseek.com/chat/completions",
        api_key="unit-test-secret",
        model="deepseek-v4-pro",
        seed_supported=False,
        timeout=120.0,
        extra_body={"thinking": {"type": "disabled"}},
    )


def _response_for_prompt(
    _self: OpenAICompatibleGenerator, prompt: str, **_kwargs: object
) -> GenerationResponse:
    options = live._option_ids_from_prompt(prompt)
    return GenerationResponse(
        expression=options[0],
        input_tokens=100,
        output_tokens=4,
        latency_ms=1.0,
        provider_request_count=1,
        seed_supported=False,
        provider_model="deepseek-v4-pro",
        finish_reason="stop",
        reasoning_tokens=0,
        candidate_format="json_expression",
    )


def _invalid_response_for_prompt(
    _self: OpenAICompatibleGenerator, prompt: str, **_kwargs: object
) -> GenerationResponse:
    options = live._option_ids_from_prompt(prompt)
    invalid = "QFFFFFFFF"
    if invalid in options:
        invalid = "Q00000000"
    return GenerationResponse(
        expression=invalid,
        input_tokens=100,
        output_tokens=4,
        latency_ms=1.0,
        provider_request_count=1,
        seed_supported=False,
        provider_model="deepseek-v4-pro",
        finish_reason="stop",
        reasoning_tokens=0,
        candidate_format="json_expression",
    )


class LiveCoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = live.load_frozen_config()
        cls.plan = live.build_live_plan(
            cls.config,
            live_config_file_sha256=live.CONFIG_FILE_SHA256,
            live_source_manifest_sha256="a" * 64,
            live_source_freeze_git_head="0" * 40,
        )
        cls.generator = _generator()
        with (
            mock.patch.object(
                OpenAICompatibleGenerator, "generate", new=_response_for_prompt
            ),
            mock.patch.object(live, "_assert_current_live_source"),
        ):
            cls.canary = live.run_target_free_canary(
                cls.plan,
                cls.generator,
                config=cls.config,
                execute=True,
            )
        binding = cls.config["benchmark_binding"]
        cls.public_path = (
            live.PROJECT_ROOT / binding["public_manifest_relative_path"]
        )

    def _write_sealed_inputs(self, directory: Path) -> dict[str, object]:
        plan_path = directory / "plan.json"
        plan_file_sha256 = live._write_json_exclusive_0600(self.plan, plan_path)
        canary_path = directory / "canary.json"
        canary_file_sha256 = live._write_json_exclusive_0600(
            self.canary, canary_path
        )
        authorization = live.build_authorization(
            self.config,
            self.plan,
            self.canary,
            live_plan_file_sha256=plan_file_sha256,
            canary_file_sha256=canary_file_sha256,
            human_exchangeability_approved=True,
        )
        authorization_path = directory / "authorization.json"
        authorization_file_sha256 = live._write_json_exclusive_0600(
            authorization, authorization_path
        )
        return {
            "plan_path": plan_path,
            "plan_file_sha256": plan_file_sha256,
            "canary_path": canary_path,
            "canary_file_sha256": canary_file_sha256,
            "authorization_path": authorization_path,
            "authorization_file_sha256": authorization_file_sha256,
        }

    def _run_kwargs(
        self, sealed: dict[str, object], directory: Path, ledger_name: str
    ) -> dict[str, object]:
        return {
            "plan_path": sealed["plan_path"],
            "expected_plan_file_sha256": sealed["plan_file_sha256"],
            "canary_path": sealed["canary_path"],
            "expected_canary_file_sha256": sealed["canary_file_sha256"],
            "authorization_path": sealed["authorization_path"],
            "expected_authorization_file_sha256": sealed[
                "authorization_file_sha256"
            ],
            "public_manifest_path": self.public_path,
            "attempt_ledger_path": directory / ledger_name,
            "generator": self.generator,
            "config": self.config,
            "project_root": live.PROJECT_ROOT,
            "execute": True,
        }

    def test_config_seal_rejects_object_and_file_tampering(self) -> None:
        tampered = copy.deepcopy(self.config)
        tampered["request_contract"]["temperature"] = 0.21
        with self.assertRaisesRegex(live.PrimaryLiveError, "canonical seal"):
            live.validate_config(tampered)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tampered-config.json"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(live.PrimaryLiveError, "file SHA-256"):
                live.load_frozen_config(path)

    def test_target_free_canary_design_has_four_pairs_eight_calls_and_no_overlap(
        self,
    ) -> None:
        design = self.plan["target_free_paired_canary"]
        self.assertEqual(design["pair_count"], 4)
        self.assertEqual(design["task_count"], 8)
        self.assertEqual(len(design["pairs"]), 4)
        self.assertEqual(len(design["schedule"]), 8)
        self.assertFalse(design["target_drawn_or_derived"])
        self.assertFalse(design["K1_K2_K3_K4_evaluated"])
        self.assertFalse(design["formal_benchmark_task_content_used"])
        self.assertFalse(design["private_key_read"])

        forbidden_fields = {
            "target_seed",
            "target_index",
            "endpoint_flags",
            "K1",
            "K2",
            "K3",
            "K4",
        }
        canary_task_ids: set[str] = set()
        canary_prompt_hashes: set[str] = set()
        for pair in design["pairs"]:
            self.assertEqual(set(pair["arms"]), {"context_a", "context_b"})
            for task in pair["arms"].values():
                self.assertTrue(forbidden_fields.isdisjoint(_all_keys(task)))
                self.assertNotRegex(
                    task["rendered_prompt"],
                    r"\b(?:target_seed|target_index|endpoint_flags|K[1-4])\b",
                )
                self.assertEqual(len(task["opaque_option_ids"]), 10)
                self.assertEqual(len(set(task["opaque_option_ids"])), 10)
                canary_task_ids.add(task["task_id"])
                canary_prompt_hashes.add(task["prompt_sha256"])

        binding = self.config["benchmark_binding"]
        public, _ = live._read_bound_json(
            live.PROJECT_ROOT / binding["public_manifest_relative_path"],
            binding["public_manifest_file_sha256"],
            "public manifest",
        )
        formal_task_ids = {task["task_id"] for task in public["tasks"]}
        formal_prompt_hashes = {task["prompt_sha256"] for task in public["tasks"]}
        self.assertTrue(canary_task_ids.isdisjoint(formal_task_ids))
        self.assertTrue(canary_prompt_hashes.isdisjoint(formal_prompt_hashes))

    def test_fake_generation_response_canary_pass_and_invalid_canary_cannot_authorize(
        self,
    ) -> None:
        with (
            mock.patch.object(
                OpenAICompatibleGenerator, "generate", new=_response_for_prompt
            ),
            mock.patch.object(live, "_assert_current_live_source"),
        ):
            passed = live.run_target_free_canary(
                self.plan,
                self.generator,
                config=self.config,
                execute=True,
            )
        self.assertTrue(passed["passed"])
        self.assertEqual(passed["call_count"], live.CANARY_TASK_COUNT)
        self.assertFalse(passed["private_key_read"])
        self.assertFalse(passed["primary_calls_authorized"])

        with (
            mock.patch.object(
                OpenAICompatibleGenerator,
                "generate",
                new=_invalid_response_for_prompt,
            ),
            mock.patch.object(live, "_assert_current_live_source"),
        ):
            failed = live.run_target_free_canary(
                self.plan,
                self.generator,
                config=self.config,
                execute=True,
            )
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["valid_choice_count"], 0)
        with self.assertRaisesRegex(
            live.PrimaryLiveError, "failed target-free canary"
        ):
            live.build_authorization(
                self.config,
                self.plan,
                failed,
                live_plan_file_sha256="b" * 64,
                canary_file_sha256="c" * 64,
                human_exchangeability_approved=True,
            )

    def test_canary_transport_failure_on_third_call_stops_without_retry(self) -> None:
        calls: list[str] = []

        def fail_on_third(
            _self: OpenAICompatibleGenerator, prompt: str, **_kwargs: object
        ) -> GenerationResponse:
            calls.append(prompt)
            if len(calls) == 3:
                raise TransportError(category="timeout", delivery_ambiguous=True)
            return _response_for_prompt(_self, prompt)

        with (
            mock.patch.object(
                OpenAICompatibleGenerator, "generate", new=fail_on_third
            ),
            mock.patch.object(live, "_assert_current_live_source"),
        ):
            with self.assertRaises(TransportError):
                live.run_target_free_canary(
                    self.plan,
                    self.generator,
                    config=self.config,
                    execute=True,
                )
        self.assertEqual(len(calls), 3)

    def test_primary_listed_option_miss_is_received_invalid_for_all_48_without_retry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sealed = self._write_sealed_inputs(directory)
            kwargs = self._run_kwargs(sealed, directory, "invalid-content.jsonl")
            with mock.patch.object(
                OpenAICompatibleGenerator,
                "generate",
                new=_invalid_response_for_prompt,
            ), mock.patch.object(live, "_assert_current_live_source"):
                bundle = live.run_primary(**kwargs)

            self.assertEqual(len(bundle["records"]), live.TASK_COUNT)
            self.assertEqual(bundle["provider_calls_made"], live.TASK_COUNT)
            self.assertEqual(bundle["transport_or_missing_failure_count"], 0)
            self.assertFalse(bundle["retry_or_resume"])
            self.assertTrue(
                all(
                    record["received"] is True
                    and record["valid_choice"] is False
                    and record["selected_option_id"] is None
                    and record["invalid_reason"]
                    == "expression_not_listed_opaque_option"
                    and record["response"]["candidate_parse_status"]
                    == "received_invalid"
                    for record in bundle["records"]
                )
            )
            rows = [
                json.loads(line)
                for line in Path(kwargs["attempt_ledger_path"])
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                sum(row["record_type"] == "response" for row in rows),
                live.TASK_COUNT,
            )
            self.assertFalse(any(row["record_type"] == "failure" for row in rows))
            self.assertEqual(rows[-1]["record_type"], "complete")

    def test_preflight_route_or_request_mismatch_makes_zero_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sealed = self._write_sealed_inputs(directory)
            variants = (
                (
                    "route",
                    OpenAICompatibleGenerator(
                        base_url="https://api.deepseek.com/chat/completions",
                        api_key="unit-test-secret",
                        model="wrong-route-model",
                        seed_supported=False,
                        timeout=120.0,
                        extra_body={"thinking": {"type": "disabled"}},
                    ),
                    "runtime request model differs",
                ),
                (
                    "request",
                    OpenAICompatibleGenerator(
                        base_url="https://api.deepseek.com/chat/completions",
                        api_key="unit-test-secret",
                        model="deepseek-v4-pro",
                        seed_supported=False,
                        timeout=60.0,
                        extra_body={"thinking": {"type": "disabled"}},
                    ),
                    "runtime sanitized request contract differs",
                ),
            )
            for label, generator, message in variants:
                with self.subTest(contract=label):
                    with mock.patch.object(
                        OpenAICompatibleGenerator,
                        "generate",
                        side_effect=AssertionError("preflight called provider"),
                    ) as generate, mock.patch.object(
                        live, "_assert_current_live_source"
                    ):
                        with self.assertRaisesRegex(
                            live.PrimaryLiveError, message
                        ):
                            live.preflight_primary(
                                plan_path=sealed["plan_path"],
                                expected_plan_file_sha256=sealed[
                                    "plan_file_sha256"
                                ],
                                canary_path=sealed["canary_path"],
                                expected_canary_file_sha256=sealed[
                                    "canary_file_sha256"
                                ],
                                authorization_path=sealed["authorization_path"],
                                expected_authorization_file_sha256=sealed[
                                    "authorization_file_sha256"
                                ],
                                public_manifest_path=self.public_path,
                                generator=generator,
                                config=self.config,
                                project_root=live.PROJECT_ROOT,
                            )
                    generate.assert_not_called()

    def test_authorization_requires_explicit_human_exchangeability_gate(self) -> None:
        with self.assertRaisesRegex(live.PrimaryLiveError, "human exchangeability"):
            live.build_authorization(
                self.config,
                self.plan,
                self.canary,
                live_plan_file_sha256="b" * 64,
                canary_file_sha256="c" * 64,
                human_exchangeability_approved=False,
            )
        authorization = live.build_authorization(
            self.config,
            self.plan,
            self.canary,
            live_plan_file_sha256="b" * 64,
            canary_file_sha256="c" * 64,
            human_exchangeability_approved=True,
        )
        self.assertTrue(authorization["primary_calls_authorized"])
        self.assertTrue(authorization["exchangeability"]["human_approval_recorded"])

    def test_analysis_rejects_incomplete_generation_before_private_key_open(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sealed = self._write_sealed_inputs(directory)
            with mock.patch.object(live, "_read_bound_private_key") as read_private:
                with self.assertRaisesRegex(
                    live.PrimaryLiveError, "complete generation bundle"
                ):
                    live.analyze_primary(
                        plan_path=sealed["plan_path"],
                        expected_plan_file_sha256=sealed["plan_file_sha256"],
                        canary_path=sealed["canary_path"],
                        expected_canary_file_sha256=sealed[
                            "canary_file_sha256"
                        ],
                        authorization_path=sealed["authorization_path"],
                        expected_authorization_file_sha256=sealed[
                            "authorization_file_sha256"
                        ],
                        public_manifest_path=self.public_path,
                        generation_path=directory / "missing-generation.json",
                        expected_generation_file_sha256="d" * 64,
                        private_key_path=(
                            live.PROJECT_ROOT
                            / self.config["benchmark_binding"][
                                "private_key_relative_path"
                            ]
                        ),
                        config=self.config,
                        project_root=live.PROJECT_ROOT,
                        require_current_source=False,
                    )
            read_private.assert_not_called()

    def test_public_only_fake_run_has_48_calls_and_exclusive_0600_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sealed = self._write_sealed_inputs(directory)
            kwargs = self._run_kwargs(sealed, directory, "attempt-ledger.jsonl")
            calls: list[str] = []

            def fake_generate(
                _self: OpenAICompatibleGenerator, prompt: str, **_kwargs: object
            ) -> GenerationResponse:
                calls.append(prompt)
                return _response_for_prompt(_self, prompt)

            real_read_bound_json = live._read_bound_json

            def guard_private_read(
                path: str | Path, expected: str, label: str
            ) -> tuple[dict[str, object], bytes]:
                if "private" in label.lower():
                    raise AssertionError("private input was opened during generation")
                return real_read_bound_json(path, expected, label)

            with (
                mock.patch.object(
                    OpenAICompatibleGenerator, "generate", new=fake_generate
                ),
                mock.patch.object(
                    live, "_read_bound_json", new=guard_private_read
                ),
                mock.patch.object(live, "_assert_current_live_source"),
            ):
                bundle = live.run_primary(**kwargs)
                self.assertEqual(len(calls), live.TASK_COUNT)
                with self.assertRaisesRegex(
                    live.PrimaryLiveError, "refusing to overwrite primary attempt ledger"
                ):
                    live.run_primary(**kwargs)

            self.assertTrue(bundle["complete"])
            self.assertFalse(bundle["private_key_read"])
            self.assertEqual(bundle["call_count"], live.TASK_COUNT)
            ledger = kwargs["attempt_ledger_path"]
            self.assertEqual(stat.S_IMODE(Path(ledger).stat().st_mode), 0o600)
            rows = [
                json.loads(line)
                for line in Path(ledger).read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), live.TASK_COUNT + 2)
            self.assertEqual(rows[0]["record_type"], "header")
            self.assertEqual(
                sum(row["record_type"] == "response" for row in rows),
                live.TASK_COUNT,
            )
            self.assertEqual(rows[-1]["record_type"], "complete")
            self.assertTrue(
                all(
                    row.get("private_key_read") is False
                    for row in rows
                    if "private_key_read" in row
                )
            )

    def test_transport_failure_stops_without_retry_and_leaves_audit_only_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            sealed = self._write_sealed_inputs(directory)
            kwargs = self._run_kwargs(sealed, directory, "failed-attempt.jsonl")
            calls: list[str] = []

            def fail_on_third(
                _self: OpenAICompatibleGenerator, prompt: str, **_kwargs: object
            ) -> GenerationResponse:
                calls.append(prompt)
                if len(calls) == 3:
                    raise TransportError(
                        category="timeout", delivery_ambiguous=True
                    )
                return _response_for_prompt(_self, prompt)

            with mock.patch.object(
                OpenAICompatibleGenerator, "generate", new=fail_on_third
            ), mock.patch.object(live, "_assert_current_live_source"):
                with self.assertRaisesRegex(
                    live.PrimaryLiveError, "non-evaluable under frozen failure policy"
                ):
                    live.run_primary(**kwargs)

            self.assertEqual(len(calls), 3)
            rows = [
                json.loads(line)
                for line in Path(kwargs["attempt_ledger_path"])
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(rows), 4)  # header, two responses, one failure
            self.assertEqual(rows[-1]["record_type"], "failure")
            self.assertEqual(rows[-1]["completed_response_count"], 2)
            self.assertFalse(rows[-1]["retry_or_resume"])
            self.assertFalse(rows[-1]["private_key_read"])
            self.assertEqual(rows[-1]["failure"]["category"], "transport_error")
            self.assertFalse(any(row["record_type"] == "complete" for row in rows))

    def test_private_scoring_reports_favorable_adverse_and_tie_directions(self) -> None:
        private = {"pairs": []}
        records: dict[str, dict[str, object]] = {}
        cases = (
            ("favorable", "AOWN", "BOWN"),
            ("adverse", "BOWN", "AOWN"),
            ("tie", "AOWN", "AOWN"),
        )
        for ordinal, (expected, selected_a, selected_b) in enumerate(cases):
            task_a = f"A-{ordinal}"
            task_b = f"B-{ordinal}"
            private["pairs"].append(
                {
                    "pair_ordinal": ordinal,
                    "pair_id": f"PAIR-{ordinal}",
                    "construction_stratum": list(live.spark_lineage.MOTIF_STRATA)[
                        ordinal
                    ],
                    "arms": {
                        "context_a": {
                            "task_id": task_a,
                            "correct_option_ids": ["AOWN"],
                        },
                        "context_b": {
                            "task_id": task_b,
                            "correct_option_ids": ["BOWN"],
                        },
                    },
                }
            )
            records[task_a] = {
                "valid_choice": True,
                "selected_option_id": selected_a,
            }
            records[task_b] = {
                "valid_choice": True,
                "selected_option_id": selected_b,
            }

        rows, totals, strata = live._score_private_pairs(private, records)
        self.assertEqual(
            [row["classification"] for row in rows],
            [expected for expected, _a, _b in cases],
        )
        self.assertEqual(totals["favorable"], 1)
        self.assertEqual(totals["adverse"], 1)
        self.assertEqual(totals["tie"], 1)
        self.assertEqual(totals["signed_total"], 0)
        self.assertEqual(totals["received_invalid_world"], 0)
        self.assertEqual(totals["complete_switch"], 1)
        self.assertTrue(
            all(
                value["world_count"] == 1
                for value in strata.values()
                if value["world_count"]
            )
        )

    def test_cli_without_execute_does_not_load_credentials_or_call_model(self) -> None:
        commands = (
            [
                "canary",
                "--plan",
                "missing-plan.json",
                "--expected-plan-file-sha256",
                "a" * 64,
                "--output",
                "missing-canary.json",
            ],
            [
                "run",
                "--plan",
                "missing-plan.json",
                "--expected-plan-file-sha256",
                "a" * 64,
                "--canary",
                "missing-canary.json",
                "--expected-canary-file-sha256",
                "b" * 64,
                "--authorization",
                "missing-authorization.json",
                "--expected-authorization-file-sha256",
                "c" * 64,
                "--public",
                "missing-public.json",
                "--attempt-ledger",
                "missing-ledger.jsonl",
                "--output",
                "missing-generation.json",
            ],
        )
        for argv in commands:
            with self.subTest(command=argv[0]):
                stderr = io.StringIO()
                with (
                    mock.patch.object(live, "load_frozen_config") as load_config,
                    mock.patch.object(
                        live, "load_provider_credentials"
                    ) as load_credentials,
                    mock.patch.object(live, "_load_cli_generator") as load_generator,
                    mock.patch.object(OpenAICompatibleGenerator, "generate") as generate,
                    redirect_stderr(stderr),
                ):
                    with self.assertRaises(SystemExit) as raised:
                        live.main(argv)
                self.assertEqual(raised.exception.code, 2)
                load_config.assert_not_called()
                load_credentials.assert_not_called()
                load_generator.assert_not_called()
                generate.assert_not_called()
                self.assertIn("requires --execute", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
