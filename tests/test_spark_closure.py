from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

from src.credentials import ProviderCredentials
from src.spark_calibration import (
    SHORTEST_PARENT,
    build_calibration_context,
    run_calibration_trajectory,
)
from src.spark_closure import (
    CLOSURE_EVIDENCE_SCOPE,
    CLOSURE_EXPECTED_CALLS,
    CLOSURE_EXPECTED_FACTUAL_CALLS,
    CLOSURE_MAX_OUTPUT_TOKENS,
    CLOSURE_TEMPERATURE,
    CLOSURE_WORLD_SEEDS,
    LAYERED_CANARY_SHA256,
    LAYERED_EVIDENCE_SCOPE,
    LAYERED_EXPECTED_CALLS,
    LAYERED_EXPECTED_FACTUAL_CALLS,
    LAYERED_MOTIF_SELECTION_NAMESPACE,
    LAYERED_PRIOR_PROSPECTIVE_V2,
    LAYERED_PROTOCOL_ID,
    LAYERED_TARGET_SEED_NAMESPACE,
    LAYERED_WORLD_SEEDS,
    PROSPECTIVE_EVIDENCE_SCOPE,
    PROSPECTIVE_MOTIF_SELECTION_NAMESPACE,
    PROSPECTIVE_PROTOCOL_ID,
    PROSPECTIVE_REPLICATION_OF,
    PROSPECTIVE_TARGET_SEED_NAMESPACE,
    PROSPECTIVE_WORLD_SEEDS,
    PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT,
    PROSPECTIVE_V2_CANARY_SHA256,
    PROSPECTIVE_V2_EVIDENCE_SCOPE,
    PROSPECTIVE_V2_MOTIF_SELECTION_NAMESPACE,
    PROSPECTIVE_V2_PRIOR_NON_EVALUABLE_ATTEMPT,
    PROSPECTIVE_V2_PROTOCOL_ID,
    PROSPECTIVE_V2_ROUTE_BINDING_SHA256,
    PROSPECTIVE_V2_TARGET_SEED_NAMESPACE,
    PROSPECTIVE_V2_WORLD_SEEDS,
    ClosureError,
    analyze_closure,
    build_closure_plan,
    build_closure_prompt,
    classify_closure_outcome,
    classify_layered_outcome,
    derive_closure_target_seed,
    generate_closure,
    main,
    parse_action,
    summarize_layered_endpoints,
    validate_closure_canary,
)
from src.spark_compressor import SparkCompressor
from src.spark_lineage import (
    MOTIF_STRATA,
    build_motif_library,
    enumerate_reachable_children,
    select_parent,
)
from src.spark_world import generate_spark_world


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reseal_generation(generation: dict[str, object]) -> None:
    unsigned = {
        key: value
        for key, value in generation.items()
        if key != "generation_sha256"
    }
    generation["generation_sha256"] = _sha256_json(unsigned)


def _wire_action(record) -> str:
    action = record.action
    path = " ".join(str(item) for item in action.path)
    if action.operation == "replace":
        return f"(edit replace {path})"
    return (
        f"(edit wrap_binary {path} "
        f"{action.binary_operator} {action.motif_side})"
    )


class _RecordingGenerator:
    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, object]] = []

    def generate(self, prompt: str, **kwargs: object) -> dict[str, object]:
        call_index = len(self.calls)
        if call_index >= len(self.script):
            raise AssertionError("closure attempted an unplanned generation call")
        self.calls.append({"prompt": prompt, **kwargs})
        return {
            "expression": self.script[call_index],
            "input_tokens": 100 + call_index,
            "output_tokens": 7,
            "latency_ms": 1.5,
            "provider_request_count": 1,
            "seed_supported": False,
            "provider_model": "fake-deepseek-v4-flash",
            "finish_reason": "stop",
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100 + call_index,
            "reasoning_tokens": 0,
            "candidate_format": "json_expression",
        }


class SparkClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = build_closure_plan()

        # Construct a target-blind integration script.  For each assigned motif
        # use the first frozen reachable action when one exists.  A motif with
        # no action is deliberately retained as a factual no-op; it is not
        # redrawn from a more convenient motif.
        lineages_by_seed = {}
        script: list[str] = []
        scripted_valid_lineages = 0
        for slot in cls.plan["slots"]:
            if slot["condition"] == "neutral":
                script.append("(no_op)")
                continue
            seed = slot["world_seed"]
            if seed not in lineages_by_seed:
                target_blind_world = generate_spark_world(seed, target_seed=0)
                lineages_by_seed[seed] = enumerate_reachable_children(
                    target_blind_world
                )
            matches = [
                record
                for record in lineages_by_seed[seed]
                if record.motif_id == slot["motif_id"]
            ]
            if matches:
                script.append(_wire_action(matches[0]))
                scripted_valid_lineages += 1
            else:
                script.append("(no_op)")

        cls.scripted_valid_lineages = scripted_valid_lineages
        cls.generator = _RecordingGenerator(script)
        cls.generation = generate_closure(cls.plan, cls.generator)
        cls.analysis = analyze_closure(cls.plan, cls.generation)

    def test_default_plan_freezes_six_worlds_and_twenty_four_slots(self) -> None:
        self.assertEqual(tuple(self.plan["world_seeds"]), CLOSURE_WORLD_SEEDS)
        self.assertEqual(CLOSURE_WORLD_SEEDS, tuple(range(3000, 3006)))
        self.assertEqual(len(self.plan["worlds"]), 6)
        self.assertEqual(len(self.plan["slots"]), CLOSURE_EXPECTED_CALLS)
        self.assertEqual(CLOSURE_EXPECTED_CALLS, 24)

        by_world: dict[int, list[dict[str, object]]] = {}
        for slot in self.plan["slots"]:
            by_world.setdefault(slot["world_seed"], []).append(slot)
        self.assertEqual(set(by_world), set(CLOSURE_WORLD_SEEDS))
        for slots in by_world.values():
            self.assertEqual([slot["slot_index"] for slot in slots], [0, 1, 2, 3])
            self.assertEqual(
                [slot["condition"] for slot in slots],
                ["neutral", "motif", "motif", "motif"],
            )
        self.assertEqual(
            sum(slot["condition"] == "motif" for slot in self.plan["slots"]),
            CLOSURE_EXPECTED_FACTUAL_CALLS,
        )

    def test_prospective_plan_is_frozen_and_target_independent_to_construct(self) -> None:
        # Building the prospective generation plan may inspect only the
        # target-blind bank/D0.  In particular, this test must never construct
        # a world using the newly derived hidden target seed.
        with mock.patch(
            "src.spark_closure.generate_spark_world",
            wraps=generate_spark_world,
        ) as build_world:
            prospective = build_closure_plan(protocol_id=PROSPECTIVE_PROTOCOL_ID)

        self.assertEqual(tuple(prospective["world_seeds"]), PROSPECTIVE_WORLD_SEEDS)
        self.assertEqual(PROSPECTIVE_WORLD_SEEDS, tuple(range(10000, 10006)))
        self.assertEqual(prospective["protocol_id"], PROSPECTIVE_PROTOCOL_ID)
        self.assertEqual(
            prospective["target_seed_namespace"],
            PROSPECTIVE_TARGET_SEED_NAMESPACE,
        )
        self.assertEqual(
            prospective["motif_selection_namespace"],
            PROSPECTIVE_MOTIF_SELECTION_NAMESPACE,
        )
        self.assertEqual(prospective["evidence_scope"], PROSPECTIVE_EVIDENCE_SCOPE)
        self.assertEqual(prospective["replication_of"], PROSPECTIVE_REPLICATION_OF)
        self.assertEqual(len(prospective["worlds"]), 6)
        self.assertEqual(len(prospective["slots"]), 24)
        self.assertEqual(build_world.call_count, 6)
        for call in build_world.call_args_list:
            self.assertEqual(call.kwargs, {"target_seed": 0})

        for world in prospective["worlds"]:
            self.assertNotIn("target_seed", world)
            self.assertNotIn("target_seed_namespace_sha256", world)

        # Motifs intentionally retain the development namespace, so only the
        # new world seed changes the deterministic assignment.
        for slot in prospective["slots"]:
            prompt = build_closure_prompt(prospective, slot)
            self.assertNotIn(PROSPECTIVE_TARGET_SEED_NAMESPACE, prompt)
            for forbidden_word in ("target", "private", "oracle", "evidence pool", "test"):
                self.assertNotIn(forbidden_word, prompt.lower())
            if slot["condition"] == "neutral":
                self.assertIsNone(slot["motif_selection_sha256"])
                continue
            namespace = (
                f"spark-closure-v1:{slot['world_seed']}:"
                f"{slot['slot_index']}:{slot['motif_stratum']}"
            )
            self.assertEqual(
                slot["motif_selection_sha256"],
                hashlib.sha256(namespace.encode("ascii")).hexdigest(),
            )
            self.assertIn(slot["motif"], prompt)

        repeated = build_closure_plan(protocol_id=PROSPECTIVE_PROTOCOL_ID)
        self.assertEqual(repeated["plan_sha256"], prospective["plan_sha256"])

        # A fake-provider artifact is useful for transport-free plumbing tests,
        # but must not unlock the unopened prospective targets.
        diagnostic_generation = generate_closure(
            prospective,
            _RecordingGenerator(["(no_op)"] * CLOSURE_EXPECTED_CALLS),
        )
        self.assertFalse(
            diagnostic_generation["live_response_contract_validated"]
        )
        with mock.patch("src.spark_closure.generate_spark_world") as hidden_world:
            with self.assertRaises(ClosureError):
                analyze_closure(prospective, diagnostic_generation)
        hidden_world.assert_not_called()

    def test_prospective_v2_plan_and_fake_barrier_are_target_independent(self) -> None:
        # This test is deliberately generation-only.  A derived v2 target seed
        # is a test failure, and neither analyzer nor compressor is invoked.
        with (
            mock.patch(
                "src.spark_closure._derive_target_seed",
                side_effect=AssertionError("v2 target seed was opened"),
            ) as derive_target,
            mock.patch(
                "src.spark_closure.generate_spark_world",
                wraps=generate_spark_world,
            ) as build_world,
        ):
            prospective = build_closure_plan(
                protocol_id=PROSPECTIVE_V2_PROTOCOL_ID
            )
            fake = _RecordingGenerator(["(no_op)"] * CLOSURE_EXPECTED_CALLS)
            diagnostic_generation = generate_closure(prospective, fake)

        derive_target.assert_not_called()
        self.assertEqual(build_world.call_count, 6)
        self.assertTrue(
            all(call.kwargs == {"target_seed": 0} for call in build_world.call_args_list)
        )
        self.assertEqual(
            tuple(prospective["world_seeds"]), PROSPECTIVE_V2_WORLD_SEEDS
        )
        self.assertEqual(PROSPECTIVE_V2_WORLD_SEEDS, tuple(range(10010, 10016)))
        self.assertEqual(prospective["protocol_id"], PROSPECTIVE_V2_PROTOCOL_ID)
        self.assertEqual(
            prospective["target_seed_namespace"],
            PROSPECTIVE_V2_TARGET_SEED_NAMESPACE,
        )
        self.assertEqual(
            prospective["motif_selection_namespace"],
            PROSPECTIVE_V2_MOTIF_SELECTION_NAMESPACE,
        )
        self.assertEqual(prospective["evidence_scope"], PROSPECTIVE_V2_EVIDENCE_SCOPE)
        self.assertEqual(prospective["replication_of"], PROSPECTIVE_REPLICATION_OF)
        self.assertEqual(
            prospective["prior_non_evaluable_attempt"],
            PROSPECTIVE_V2_PRIOR_NON_EVALUABLE_ATTEMPT,
        )
        self.assertEqual(
            prospective["prior_non_evaluable_attempt"]["plan_sha256"],
            "60003e4ea397456faf981bc5a954396760257e66b851ef942a819f132cb00f28",
        )
        self.assertEqual(
            prospective["model_route"]["canary_artifact_sha256"],
            PROSPECTIVE_V2_CANARY_SHA256,
        )
        self.assertEqual(
            prospective["model_route"]["route_binding_sha256"],
            PROSPECTIVE_V2_ROUTE_BINDING_SHA256,
        )
        self.assertEqual(len(prospective["worlds"]), 6)
        self.assertEqual(len(prospective["slots"]), CLOSURE_EXPECTED_CALLS)
        self.assertTrue(
            all(
                "target_seed" not in world
                and "target_seed_namespace_sha256" not in world
                for world in prospective["worlds"]
            )
        )
        self.assertEqual(len(fake.calls), CLOSURE_EXPECTED_CALLS)
        self.assertFalse(
            diagnostic_generation["live_response_contract_validated"]
        )
        self.assertEqual(
            diagnostic_generation["canary_artifact_sha256"],
            PROSPECTIVE_V2_CANARY_SHA256,
        )
        self.assertEqual(
            diagnostic_generation["route_binding_sha256"],
            PROSPECTIVE_V2_ROUTE_BINDING_SHA256,
        )

        repeated = build_closure_plan(protocol_id=PROSPECTIVE_V2_PROTOCOL_ID)
        self.assertEqual(repeated["plan_sha256"], prospective["plan_sha256"])

    def test_prospective_v2_explicit_canary_matches_its_plan_contract(self) -> None:
        credentials = ProviderCredentials(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="unused-local-validation-key",
        )
        canary_path = (
            Path(__file__).resolve().parents[1]
            / "artifacts"
            / "v3-canaries-20260813-r3"
            / "deepseek-official.json"
        )
        _generator, contract, binding = validate_closure_canary(
            credentials,
            canary_path,
            protocol_id=PROSPECTIVE_V2_PROTOCOL_ID,
        )
        self.assertEqual(contract.to_dict(), PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT)
        self.assertEqual(
            binding["canary_evidence"]["artifact_sha256"],
            PROSPECTIVE_V2_CANARY_SHA256,
        )
        self.assertEqual(
            binding["canary_evidence"]["route_binding_sha256"],
            PROSPECTIVE_V2_ROUTE_BINDING_SHA256,
        )

        # The CLI exposes v2 as a plan choice; this remains target-independent.
        with mock.patch("src.spark_closure._emit_json") as emit:
            self.assertEqual(
                main(["plan", "--protocol", PROSPECTIVE_V2_PROTOCOL_ID]), 0
            )
        self.assertEqual(emit.call_args.args[0]["protocol_id"], PROSPECTIVE_V2_PROTOCOL_ID)

    def test_prospective_classification_is_a_pure_count_rule(self) -> None:
        cases = (
            (1, 1, "prospective_mechanism_instance_replicated"),
            (0, 7, "prospective_replication_not_observed"),
            (0, 0, "prospective_lineage_interface_failure"),
        )
        for strict_events, valid_lineages, expected in cases:
            with self.subTest(
                strict_events=strict_events, valid_lineages=valid_lineages
            ):
                self.assertEqual(
                    classify_closure_outcome(
                        protocol_id=PROSPECTIVE_PROTOCOL_ID,
                        strict_event_count=strict_events,
                        valid_lineage_count=valid_lineages,
                    ),
                    expected,
                )

    def test_target_and_motif_schedules_use_independent_frozen_namespaces(self) -> None:
        for world in self.plan["worlds"]:
            seed = world["world_seed"]
            target_payload = f"spark-closure-v1:target:{seed}".encode("ascii")
            self.assertEqual(
                world["target_seed"],
                int.from_bytes(hashlib.sha256(target_payload).digest(), "big"),
            )
            self.assertEqual(
                world["target_seed_namespace_sha256"],
                hashlib.sha256(target_payload).hexdigest(),
            )

        motifs_by_stratum = {
            stratum: tuple(
                sorted(
                    (
                        motif
                        for motif in build_motif_library()
                        if motif.stratum == stratum
                    ),
                    key=lambda motif: (motif.canonical_hash, motif.motif_id),
                )
            )
            for stratum in MOTIF_STRATA
        }
        factual_index = 0
        for slot in self.plan["slots"]:
            if slot["condition"] != "motif":
                continue
            expected_stratum = MOTIF_STRATA[factual_index % len(MOTIF_STRATA)]
            factual_index += 1
            self.assertEqual(slot["motif_stratum"], expected_stratum)
            namespace = (
                f"spark-closure-v1:{slot['world_seed']}:"
                f"{slot['slot_index']}:{expected_stratum}"
            )
            digest = hashlib.sha256(namespace.encode("ascii")).digest()
            choices = motifs_by_stratum[expected_stratum]
            expected = choices[int.from_bytes(digest, "big") % len(choices)]
            self.assertEqual(slot["motif_id"], expected.motif_id)
            self.assertEqual(
                slot["motif_selection_sha256"],
                hashlib.sha256(namespace.encode("ascii")).hexdigest(),
            )

        self.assertEqual(
            self.plan["stratum_counts"],
            dict(zip(MOTIF_STRATA, (5, 5, 4, 4), strict=True)),
        )

        assignments = [
            (slot["motif_id"], slot["motif_selection_sha256"])
            for slot in self.plan["slots"]
        ]
        with mock.patch(
            "src.spark_closure.derive_closure_target_seed",
            side_effect=lambda seed: derive_closure_target_seed(seed) ^ 1,
        ):
            alternate_targets = build_closure_plan()
        self.assertEqual(
            assignments,
            [
                (slot["motif_id"], slot["motif_selection_sha256"])
                for slot in alternate_targets["slots"]
            ],
        )
        self.assertNotEqual(
            [world["target_seed"] for world in self.plan["worlds"]],
            [world["target_seed"] for world in alternate_targets["worlds"]],
        )

    def test_prompts_expose_only_public_d0_parent_and_assigned_motif(self) -> None:
        for slot in self.plan["slots"]:
            prompt = build_closure_prompt(self.plan, slot)
            lowered = prompt.lower()
            for forbidden_word in ("target", "private", "oracle", "evidence pool", "test"):
                self.assertNotIn(forbidden_word, lowered)
            self.assertEqual(prompt.count(" -> "), 12)

            world_entry = next(
                world
                for world in self.plan["worlds"]
                if world["world_seed"] == slot["world_seed"]
            )
            self.assertIn(world_entry["parent"], prompt)
            self.assertNotIn(str(world_entry["target_seed"]), prompt)

            private_world = generate_spark_world(
                slot["world_seed"], world_entry["target_seed"]
            )
            for example in (*private_world.evidence, *private_world.test):
                self.assertNotIn(str(tuple(example.point)), prompt)

            if slot["condition"] == "neutral":
                self.assertIn("NULL / NO_MOTIF", prompt)
                self.assertNotIn("expected_old_subtree_hash=", prompt)
            else:
                self.assertIn(slot["motif"], prompt)
                self.assertIn(slot["motif_id"], prompt)
                self.assertEqual(prompt.count("expected_old_subtree_hash="), 2)

    def test_action_parser_accepts_only_the_frozen_closed_language(self) -> None:
        self.assertTrue(parse_action("(no_op)").is_no_op)
        for path in ((1, 1), (1, 2)):
            parsed = parse_action(f"(edit replace {path[0]} {path[1]})")
            self.assertEqual((parsed.operation, parsed.path), ("replace", path))
            for operator in ("add", "sub", "mul"):
                sides = ("left", "right") if operator == "sub" else ("right",)
                for side in sides:
                    parsed = parse_action(
                        f"(edit wrap_binary {path[0]} {path[1]} {operator} {side})"
                    )
                    self.assertEqual(parsed.operation, "wrap_binary")
                    self.assertEqual(parsed.path, path)
                    self.assertEqual(parsed.binary_operator, operator)
                    self.assertEqual(parsed.motif_side, side)

        invalid = (
            None,
            "",
            "(var x1)",
            "(edit replace 1 3)",
            "(edit replace 1 1 add)",
            "(edit replace true 1)",
            "(edit wrap_binary 1 1 div left)",
            "(edit wrap_binary 1 1 add left)",
            "(edit wrap_binary 1 1 mul left)",
            "(edit wrap_binary 1 1 add middle)",
            "(edit wrap_binary 1 3 add left)",
            "(edit wrap_binary 1 1 add left extra)",
            '{"expression":"(edit replace 1 1)"}',
        )
        for expression in invalid:
            with self.subTest(expression=expression):
                with self.assertRaises(ClosureError):
                    parse_action(expression)

    def test_generation_makes_exactly_one_fixed_budget_call_per_slot(self) -> None:
        self.assertEqual(len(self.generator.calls), CLOSURE_EXPECTED_CALLS)
        self.assertEqual(self.generation["call_count"], CLOSURE_EXPECTED_CALLS)
        for index, call in enumerate(self.generator.calls):
            self.assertEqual(call["temperature"], CLOSURE_TEMPERATURE)
            self.assertEqual(call["max_output_tokens"], CLOSURE_MAX_OUTPUT_TOKENS)
            self.assertEqual(call["round_index"], index // 4)
            self.assertEqual(call["candidate_index"], index % 4)
        self.assertEqual(
            sum(
                record["telemetry"]["provider_request_count"]
                for record in self.generation["records"]
            ),
            CLOSURE_EXPECTED_CALLS,
        )

    def test_generation_artifact_contains_no_prompt_or_private_analysis(self) -> None:
        forbidden_keys = {
            "prompt",
            "raw",
            "raw_response",
            "assistant_text",
            "target_seed",
            "target_index",
            "target_canonical_hash",
            "evidence",
            "test",
            "child_ast",
            "child_behavior_hash",
            "trajectory",
            "strict_event",
            "classification",
            "Y00",
            "Y01",
            "Y10",
            "Y11",
        }

        def visit(value: object) -> None:
            if isinstance(value, dict):
                self.assertTrue(forbidden_keys.isdisjoint(value))
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(self.generation)
        self.assertTrue(
            self.generation["generation_complete_before_target_analysis"]
        )

    def test_invalid_action_consumes_its_slot_without_retry(self) -> None:
        script = ["(no_op)"] * CLOSURE_EXPECTED_CALLS
        invalid_index = 7
        script[invalid_index] = "free-form child program"
        generator = _RecordingGenerator(script)
        generation = generate_closure(self.plan, generator)
        self.assertEqual(len(generator.calls), CLOSURE_EXPECTED_CALLS)
        record = generation["records"][invalid_index]
        self.assertFalse(record["action_parse_valid"])
        self.assertIsNone(record["action"])
        self.assertEqual(record["parse_failure"], "invalid_action_grammar")
        self.assertEqual(
            sum(not row["action_parse_valid"] for row in generation["records"]),
            1,
        )

    def test_analysis_rejects_incomplete_or_anomalous_generation_before_targets(self) -> None:
        incomplete = copy.deepcopy(self.generation)
        incomplete["records"].pop()
        incomplete["call_count"] = 23
        _reseal_generation(incomplete)

        anomalous = copy.deepcopy(self.generation)
        anomalous["records"][5]["world_seed"] = 999999
        _reseal_generation(anomalous)

        for artifact in (incomplete, anomalous):
            with self.subTest(call_count=artifact["call_count"]):
                with mock.patch("src.spark_closure.generate_spark_world") as build_world:
                    with self.assertRaises(ClosureError):
                        analyze_closure(self.plan, artifact)
                build_world.assert_not_called()

    def test_source_manifest_drift_is_enforced_only_before_live_cli_setup(self) -> None:
        drift = {"source_manifest_sha256": "0" * 64}

        # Programmatic artifact construction remains structurally usable after
        # later code drift; this fake path does not represent a live attempt.
        with mock.patch("src.spark_closure.source_manifest", return_value=drift):
            fake = _RecordingGenerator(["(no_op)"] * CLOSURE_EXPECTED_CALLS)
            generated = generate_closure(self.plan, fake)
        self.assertEqual(generated["call_count"], CLOSURE_EXPECTED_CALLS)

        # Offline analysis likewise reaches generation validation rather than
        # rejecting the historical plan solely because current source moved.
        incomplete = copy.deepcopy(self.generation)
        incomplete["records"].pop()
        incomplete["call_count"] -= 1
        _reseal_generation(incomplete)
        with mock.patch("src.spark_closure.source_manifest", return_value=drift):
            with self.assertRaisesRegex(ClosureError, "incomplete"):
                analyze_closure(self.plan, incomplete)

        # The live CLI checks the manifest before provider construction, which
        # is the first operation that could lead to a paid network request.
        with (
            mock.patch("src.spark_closure._read_json", return_value=self.plan),
            mock.patch("src.spark_closure.source_manifest", return_value=drift),
            mock.patch("src.spark_closure._live_generator") as live_generator,
        ):
            with self.assertRaisesRegex(ClosureError, "source manifest drifted"):
                main(
                    [
                        "generate",
                        "--plan",
                        "unused-plan.json",
                        "--output",
                        "unused-generation.json",
                        "--execute",
                    ]
                )
        live_generator.assert_not_called()

    def test_legacy_development_plan_without_new_identifiers_remains_readable(self) -> None:
        legacy = copy.deepcopy(self.plan)
        for field in (
            "protocol_id",
            "target_seed_namespace",
            "motif_selection_namespace",
        ):
            legacy.pop(field)
        unsigned = {
            key: value for key, value in legacy.items() if key != "plan_sha256"
        }
        legacy["plan_sha256"] = _sha256_json(unsigned)
        fake = _RecordingGenerator(["(no_op)"] * CLOSURE_EXPECTED_CALLS)
        generated = generate_closure(legacy, fake)
        self.assertEqual(generated["protocol_id"], "development-v1")
        self.assertEqual(generated["evidence_scope"], CLOSURE_EVIDENCE_SCOPE)

    def test_full_fake_run_reports_six_worlds_and_only_exploratory_classification(self) -> None:
        report = self.analysis
        self.assertEqual(len(report["worlds"]), 6)
        self.assertEqual(report["model_call_count"], 24)
        self.assertEqual(report["factual_slot_count"], 18)
        self.assertEqual(report["neutral_slot_count"], 6)
        self.assertEqual(report["neutral_no_op_valid_count"], 6)
        self.assertEqual(report["valid_lineage_count"], self.scripted_valid_lineages)
        self.assertEqual(report["evidence_scope"], CLOSURE_EVIDENCE_SCOPE)
        self.assertFalse(report["confirmatory_evidence"])
        self.assertTrue(report["strict_reachable_gate_remains_failed"])

        strict_event_count = 0
        valid_lineage_count = 0
        for world in report["worlds"]:
            self.assertEqual(len(world["slot_results"]), 4)
            self.assertFalse(world["slot_results"][0]["strict_event"])
            for row in world["slot_results"][1:]:
                valid_lineage_count += bool(row["lineage_valid"])
                if not row["lineage_valid"]:
                    self.assertFalse(row["strict_event"])
                    continue
                expected_event = all(
                    (
                        not row["child_direct_hit"],
                        row["child_trajectory"]["truth_retained"],
                        row["child_trajectory"]["N_T"] == 1,
                        row["child_trajectory"]["full_domain_recovered"],
                        not row["parent_trajectory"]["exact_identification"],
                        any(
                            not replacement["trajectory"]["exact_identification"]
                            for replacement in row["matched_replacements"]
                        ),
                        row["child_trajectory"]["positive_non_match_contraction"],
                    )
                )
                self.assertEqual(row["strict_event"], expected_event)
                strict_event_count += expected_event

        self.assertEqual(valid_lineage_count, report["valid_lineage_count"])
        self.assertEqual(strict_event_count, report["strict_event_count"])
        if strict_event_count:
            expected_classification = "exploratory_mechanism_instance_observed"
        elif valid_lineage_count:
            expected_classification = "lineage_feasible_but_no_strict_event"
        else:
            expected_classification = "model_lineage_interface_not_feasible"
        self.assertEqual(report["classification"], expected_classification)
        self.assertIn("not a confirmatory positive or negative", report["interpretation_limit"])

    def test_parent_deletion_matches_the_frozen_shortest_parent_baseline(self) -> None:
        world_seed = CLOSURE_WORLD_SEEDS[0]
        world = generate_spark_world(
            world_seed, derive_closure_target_seed(world_seed)
        )
        parent = select_parent(world)
        expected = SparkCompressor(world).run(parent, max_rounds=4)
        report_world = self.analysis["worlds"][0]
        for row in report_world["slot_results"]:
            observed = row["parent_trajectory"]
            self.assertEqual(tuple(observed["N_t"]), expected.N_t)
            self.assertEqual(observed["N_T"], expected.N_T)
            self.assertEqual(observed["truth_retained"], expected.truth_retained)
            self.assertEqual(
                observed["exact_identification"], expected.exact_identification
            )
            self.assertEqual(
                observed["full_domain_recovered"], expected.full_domain_recovered
            )
            self.assertEqual(
                observed["certified_fact_count"], expected.certified_fact_count
            )

        context = build_calibration_context(world_seed)
        calibration = run_calibration_trajectory(
            context,
            world.target_index,
            SHORTEST_PARENT,
            max_queries=4,
        )
        neutral = report_world["slot_results"][0]
        self.assertEqual(tuple(neutral["parent_trajectory"]["N_t"]), calibration.version_sizes)
        self.assertEqual(neutral["Y01"], calibration.singleton)
        self.assertEqual(neutral["Y00"], calibration.direct_hit)

    def test_layered_plan_is_target_independent_and_has_32_by_3_layout(self) -> None:
        with (
            mock.patch(
                "src.spark_closure._derive_target_seed",
                side_effect=AssertionError("layered target seed was opened"),
            ) as derive_target,
            mock.patch(
                "src.spark_closure.generate_spark_world",
                wraps=generate_spark_world,
            ) as build_world,
        ):
            plan = build_closure_plan(protocol_id=LAYERED_PROTOCOL_ID)
            fake = _RecordingGenerator(["(no_op)"] * LAYERED_EXPECTED_CALLS)
            diagnostic_generation = generate_closure(plan, fake)

        derive_target.assert_not_called()
        self.assertEqual(build_world.call_count, 32)
        self.assertTrue(
            all(
                call.kwargs == {"target_seed": 0}
                for call in build_world.call_args_list
            )
        )
        self.assertEqual(tuple(plan["world_seeds"]), LAYERED_WORLD_SEEDS)
        self.assertEqual(len(plan["worlds"]), 32)
        self.assertEqual(len(plan["slots"]), LAYERED_EXPECTED_CALLS)
        self.assertEqual(LAYERED_EXPECTED_CALLS, 96)
        self.assertEqual(LAYERED_EXPECTED_FACTUAL_CALLS, 96)
        self.assertEqual(plan["protocol_id"], LAYERED_PROTOCOL_ID)
        self.assertEqual(plan["evidence_scope"], LAYERED_EVIDENCE_SCOPE)
        self.assertEqual(
            plan["target_seed_namespace"], LAYERED_TARGET_SEED_NAMESPACE
        )
        self.assertEqual(
            plan["motif_selection_namespace"],
            LAYERED_MOTIF_SELECTION_NAMESPACE,
        )
        self.assertEqual(
            plan["follows_prospective_v2"], LAYERED_PRIOR_PROSPECTIVE_V2
        )
        self.assertNotIn("replication_of", plan)
        self.assertEqual(
            plan["stratum_counts"],
            {stratum: 24 for stratum in MOTIF_STRATA},
        )
        by_world: dict[int, list[dict[str, object]]] = {}
        for slot in plan["slots"]:
            by_world.setdefault(slot["world_seed"], []).append(slot)
        self.assertTrue(
            all(
                [slot["slot_index"] for slot in world_slots] == [1, 2, 3]
                and all(slot["condition"] == "motif" for slot in world_slots)
                for world_slots in by_world.values()
            )
        )
        self.assertTrue(
            all(
                "target_seed" not in world
                and "target_seed_namespace_sha256" not in world
                for world in plan["worlds"]
            )
        )
        self.assertFalse(diagnostic_generation["live_response_contract_validated"])
        self.assertEqual(len(fake.calls), 96)
        with mock.patch("src.spark_closure.generate_spark_world") as hidden_world:
            with self.assertRaises(ClosureError):
                analyze_closure(plan, diagnostic_generation)
        hidden_world.assert_not_called()

    def test_layered_canary_reuses_the_frozen_v2_route_without_network(self) -> None:
        credentials = ProviderCredentials(
            base_url="https://api.deepseek.com",
            model="deepseek-v4-flash",
            api_key="unused-local-validation-key",
        )
        canary_path = (
            Path(__file__).resolve().parents[1]
            / "artifacts"
            / "v3-canaries-20260814-r4"
            / "deepseek-official.json"
        )
        _generator, contract, binding = validate_closure_canary(
            credentials,
            canary_path,
            protocol_id=LAYERED_PROTOCOL_ID,
        )
        self.assertEqual(
            contract.to_dict(), PROSPECTIVE_V2_ACCEPTED_RESPONSE_CONTRACT
        )
        self.assertEqual(
            binding["canary_evidence"]["artifact_sha256"],
            LAYERED_CANARY_SHA256,
        )
        self.assertEqual(
            binding["canary_evidence"]["route_binding_sha256"],
            PROSPECTIVE_V2_ROUTE_BINDING_SHA256,
        )

    def test_layered_classification_and_synthetic_world_aggregation(self) -> None:
        self.assertEqual(
            classify_layered_outcome(0), "not_observed_under_frozen_protocol"
        )
        self.assertEqual(
            classify_layered_outcome(1),
            "single_prospective_mechanism_instance_observed",
        )
        self.assertEqual(
            classify_layered_outcome(2),
            "prospective_cross_world_replication_observed",
        )
        self.assertEqual(
            classify_layered_outcome(20),
            "prospective_cross_world_replication_observed",
        )
        for invalid in (-1, 1.0, True):
            with self.assertRaises(ClosureError):
                classify_layered_outcome(invalid)  # type: ignore[arg-type]

        def trajectory(
            N_T: int,
            *,
            exact: bool,
            positive_nonmatch: bool = True,
        ) -> dict[str, object]:
            return {
                "N_T": N_T,
                "rounds_completed": 4,
                "truth_retained": True,
                "full_domain_recovered": exact,
                "exact_identification": exact,
                "positive_non_match_contraction": positive_nonmatch,
            }

        def slot(
            slot_id: str,
            *,
            child_n: int,
            child_exact: bool,
            parent_n: int,
            parent_exact: bool,
            replacement_ns: tuple[int, int],
            replacement_exact: tuple[bool, bool],
        ) -> dict[str, object]:
            return {
                "slot_id": slot_id,
                "condition": "motif",
                "lineage_valid": True,
                "child_direct_hit": False,
                "child_trajectory": trajectory(child_n, exact=child_exact),
                "parent_trajectory": trajectory(parent_n, exact=parent_exact),
                "matched_replacements": [
                    {
                        "trajectory": trajectory(n, exact=exact),
                    }
                    for n, exact in zip(
                        replacement_ns, replacement_exact, strict=True
                    )
                ],
            }

        worlds = [
            {
                "world_seed": 1,
                "slot_results": [
                    slot(
                        "strong",
                        child_n=1,
                        child_exact=True,
                        parent_n=4,
                        parent_exact=False,
                        replacement_ns=(5, 6),
                        replacement_exact=(False, False),
                    )
                ],
            },
            {
                "world_seed": 2,
                "slot_results": [
                    slot(
                        "weak",
                        child_n=1,
                        child_exact=True,
                        parent_n=3,
                        parent_exact=False,
                        replacement_ns=(1, 4),
                        replacement_exact=(True, False),
                    )
                ],
            },
            {
                "world_seed": 3,
                "slot_results": [
                    slot(
                        "parent-closes",
                        child_n=1,
                        child_exact=True,
                        parent_n=1,
                        parent_exact=True,
                        replacement_ns=(1, 1),
                        replacement_exact=(True, True),
                    )
                ],
            },
            {
                "world_seed": 4,
                "slot_results": [
                    slot(
                        "lineage-only",
                        child_n=4,
                        child_exact=False,
                        parent_n=3,
                        parent_exact=False,
                        replacement_ns=(5, 2),
                        replacement_exact=(False, False),
                    )
                ],
            },
        ]
        summary = summarize_layered_endpoints(worlds)
        self.assertEqual(
            summary["world_counts"], {"L": 4, "M": 3, "D": 2, "R": 1, "S": 1}
        )
        self.assertEqual(
            summary["world_counts_K"], {"K1": 4, "K2": 3, "K3": 2, "K4": 1}
        )
        self.assertEqual(
            summary["deepest_layer_bottleneck"],
            "strong_matched_motif_specificity_observed",
        )
        self.assertEqual(
            summary["classification"],
            "single_prospective_mechanism_instance_observed",
        )
        self.assertEqual(summary["world_denominator"], 4)
        self.assertEqual(summary["world_rates"]["S"]["rate"], 0.25)
        self.assertEqual(
            summary["conditional_conversions_descriptive_only"],
            {
                "M_given_L": {
                    "numerator": 3,
                    "denominator": 4,
                    "rate": 0.75,
                    "estimability": "descriptive",
                },
                "D_given_M": {
                    "numerator": 2,
                    "denominator": 3,
                    "rate": 2 / 3,
                    "estimability": "descriptive",
                },
                "R_given_D": {
                    "numerator": 1,
                    "denominator": 2,
                    "rate": 0.5,
                    "estimability": "descriptive",
                },
            },
        )
        self.assertEqual(summary["weak_replacement_world_count"], 2)
        terminal = summary["terminal_N_T_differences_descriptive_only"]
        self.assertEqual(terminal["valid_lineage_slot_count"], 4)
        self.assertEqual(
            terminal["parent_minus_child"],
            {
                "count": 4,
                "positive_count": 2,
                "tie_count": 1,
                "negative_count": 1,
                "sum": 4,
                "mean": 1.0,
            },
        )
        self.assertEqual(terminal["log2_parent_over_child"]["count"], 4)
        self.assertEqual(terminal["log2_parent_over_child"]["positive_count"], 2)
        self.assertEqual(terminal["log2_parent_over_child"]["tie_count"], 1)
        self.assertEqual(terminal["log2_parent_over_child"]["negative_count"], 1)
        self.assertEqual(
            [row["endpoints"] for row in summary["worlds"]],
            [
                {"L": True, "M": True, "D": True, "R": True, "S": True},
                {"L": True, "M": True, "D": True, "R": False, "S": False},
                {"L": True, "M": True, "D": False, "R": False, "S": False},
                {"L": True, "M": False, "D": False, "R": False, "S": False},
            ],
        )

        zero = summarize_layered_endpoints(
            [{"world_seed": seed, "slot_results": []} for seed in range(32)]
        )
        upper = zero["world_rates"]["S"]["clopper_pearson_95"]["upper"]
        self.assertAlmostEqual(upper, 1.0 - 0.025 ** (1.0 / 32), places=12)
        self.assertAlmostEqual(upper, 0.1089, places=4)
        self.assertIsNone(
            zero["conditional_conversions_descriptive_only"]["M_given_L"]["rate"]
        )
        self.assertEqual(
            zero["conditional_conversions_descriptive_only"]["M_given_L"][
                "estimability"
            ],
            "not_estimable",
        )
        self.assertEqual(
            zero["deepest_layer_bottleneck"], "lineage_interface_failure"
        )

        all_success = summarize_layered_endpoints(
            [
                {
                    "world_seed": seed,
                    "slot_results": [
                        slot(
                            f"strong-{seed}",
                            child_n=1,
                            child_exact=True,
                            parent_n=2,
                            parent_exact=False,
                            replacement_ns=(2, 2),
                            replacement_exact=(False, False),
                        )
                    ],
                }
                for seed in range(32)
            ]
        )
        lower = all_success["world_rates"]["S"]["clopper_pearson_95"]["lower"]
        self.assertAlmostEqual(lower, 0.025 ** (1.0 / 32), places=12)
        self.assertAlmostEqual(lower, 1.0 - upper, places=12)

    def test_section_16_sealed_artifacts_replay_exactly(self) -> None:
        artifact_dir = (
            Path(__file__).resolve().parents[1]
            / "artifacts"
            / "spark-closure-20260813"
        )
        plan = json.loads((artifact_dir / "plan.json").read_text(encoding="utf-8"))
        generation = json.loads(
            (artifact_dir / "generation.json").read_text(encoding="utf-8")
        )
        expected = json.loads(
            (artifact_dir / "analysis.json").read_text(encoding="utf-8")
        )
        replayed = analyze_closure(plan, generation)
        self.assertEqual(replayed, expected)
        self.assertEqual(
            replayed["analysis_sha256"],
            "c3f458c5a3bb8ba44411e7fae6e9edb98868f4d9df27234a88f9a7777ffc52af",
        )


if __name__ == "__main__":
    unittest.main()
