from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import unittest
from unittest import mock

from src.experiment import (
    ARM_EXECUTION_BASE_ORDER,
    ConfigError,
    GeneratorContext,
    OfflineSmokeGeneratorFactory,
    SAMPLING_BASE_SEED,
    _arm_execution_order,
    load_config,
    run_experiment,
    validate_config,
)
from src.runner import GenerationResponse, evaluate_episode_test as finalize_episode_test


ROOT = Path(__file__).resolve().parents[1]
HIDDEN_LAW = "TOP_SECRET_HIDDEN_LAW"
PRIVATE_TEST_LABEL = 987654321
RAW_ASSISTANT_SECRET = "RAW_ASSISTANT_SECRET_MUST_NOT_PERSIST"


@dataclass(frozen=True)
class Example:
    point: tuple[int, int, int]
    label: int


@dataclass(frozen=True)
class SecretWorld:
    seed: int
    depth_tier: int
    world_hash: str
    law: str
    train: tuple[Example, ...]
    probe: tuple[Example, ...]
    test: tuple[Example, ...]


def small_config() -> dict:
    return {
        "schema_version": 1,
        "status": "development-only",
        "experiment": "orchestration-unit-test",
        "worlds": [{"seed": 41, "depth": 3}],
        "episode": {
            "rounds": 2,
            "candidates_per_round": 2,
            "max_output_tokens": 17,
            "archive_size": 2,
            "max_counterexamples_per_round": 1,
        },
        "arms": {
            "L": {"kind": "fixed", "temperature": 0.2},
            "E": {
                "kind": "adaptive",
                "initial_temperature": 1.0,
                "minimum_temperature": 0.2,
                "maximum_temperature": 1.2,
                "improvement_step": -0.2,
                "stagnation_step": 0.3,
            },
        },
        "model": {
            "provider": "unit-test",
            "name": "offline",
            "snapshot": "unit-test-snapshot",
            "structured_output": True,
        },
    }


def e2_config() -> dict:
    config = small_config()
    config["arms"] = {"E2": dict(config["arms"]["E"])}
    config["arms"]["E2"].update(
        {
            "controller_version": "validity-novelty-v2",
            "minimum_valid_candidates": 2,
            "minimum_useful_new_behaviors": 1,
            "useful_novelty_score_tolerance": 1.0 / 12.0,
            "decision_precedence": [
                "low_validity_decrease",
                "probe_improved_decrease",
                "probe_ceiling_hold",
                "useful_novelty_hold",
                "stale_search_increase",
            ],
        }
    )
    return config


def secret_world() -> SecretWorld:
    return SecretWorld(
        seed=41,
        depth_tier=3,
        world_hash="f" * 64,
        law=HIDDEN_LAW,
        train=(Example((1, 0, 0), 1), Example((-1, 0, 0), -1)),
        probe=(Example((2, 1, 0), 2), Example((-2, 1, 0), -2)),
        test=(Example((98, 97, 96), PRIVATE_TEST_LABEL),),
    )


def frozen_confirmatory_config() -> dict:
    config = load_config(ROOT / "configs" / "pilot.json")
    config["status"] = "confirmatory-frozen"
    config["worlds"] = [
        {"seed": 3000 + index, "depth": (3, 4, 5)[index % 3]}
        for index in range(40)
    ]
    config["model"] = {
        "provider": "provider",
        "name": "model-name",
        "snapshot": "snapshot-2026-08-04",
        "structured_output": True,
    }
    config["primary_comparator"] = "MTX"
    config["development_config_hash"] = "a" * 64
    config["development_results_hash"] = "b" * 64
    return config


class RecordingGenerator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(
        self,
        prompt: str,
        *,
        temperature: float,
        max_output_tokens: int,
        round_index: int,
        candidate_index: int,
        seed: int,
        state: dict,
    ) -> dict[str, str]:
        self.calls.append(
            {
                "prompt": prompt,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "round_index": round_index,
                "candidate_index": candidate_index,
                "seed": seed,
                "state": state,
            }
        )
        return {"expression": "(var x1)"}


class RecordingFactory:
    evidence = True

    def __init__(self) -> None:
        self.contexts: list[GeneratorContext] = []
        self.generators: list[RecordingGenerator] = []

    def __call__(self, context: GeneratorContext) -> RecordingGenerator:
        self.contexts.append(context)
        generator = RecordingGenerator()
        self.generators.append(generator)
        return generator


class MeteredGenerator(RecordingGenerator):
    def __init__(self, *, output_tokens: int = 2) -> None:
        super().__init__()
        self.metered_output_tokens = output_tokens

    def generate(self, *args, **kwargs) -> GenerationResponse:
        super().generate(*args, **kwargs)
        return GenerationResponse(
            expression="(var x1)",
            input_tokens=10,
            output_tokens=self.metered_output_tokens,
            latency_ms=5.0,
            provider_request_count=1,
            seed_supported=True,
            provider_model="unit-test-snapshot",
            finish_reason="stop",
            candidate_format="json_expression",
        )


class MeteredFactory:
    evidence = True

    def __init__(self, output_tokens_by_run: tuple[int, ...] = ()) -> None:
        self.output_tokens_by_run = output_tokens_by_run
        self.calls = 0

    def __call__(self, context: GeneratorContext) -> MeteredGenerator:
        del context
        output_tokens = (
            self.output_tokens_by_run[self.calls]
            if self.calls < len(self.output_tokens_by_run)
            else 2
        )
        self.calls += 1
        return MeteredGenerator(output_tokens=output_tokens)


class WrongModelGenerator(MeteredGenerator):
    def generate(self, *args, **kwargs) -> GenerationResponse:
        response = super().generate(*args, **kwargs)
        return GenerationResponse(
            expression=response.expression,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=response.latency_ms,
            provider_request_count=response.provider_request_count,
            seed_supported=response.seed_supported,
            provider_model="unexpected-provider-alias",
            finish_reason=response.finish_reason,
            candidate_format=response.candidate_format,
        )


class WrongModelFactory(MeteredFactory):
    def __call__(self, context: GeneratorContext) -> WrongModelGenerator:
        del context
        return WrongModelGenerator()


class MalformedMeteredGenerator(RecordingGenerator):
    def generate(self, *args, **kwargs) -> GenerationResponse:
        super().generate(*args, **kwargs)
        return GenerationResponse(
            expression=f"not a DSL expression {RAW_ASSISTANT_SECRET}",
            input_tokens=10,
            output_tokens=2,
            latency_ms=5.0,
            provider_request_count=1,
            seed_supported=True,
            provider_model="unit-test-snapshot",
            finish_reason="stop",
            candidate_format="invalid_json",
        )


class MalformedMeteredFactory:
    evidence = False

    def __call__(self, context: GeneratorContext) -> MalformedMeteredGenerator:
        del context
        return MalformedMeteredGenerator()


class ExperimentConfigValidationTests(unittest.TestCase):
    def test_default_pilot_is_read_and_validated(self) -> None:
        config = load_config()

        self.assertEqual(config["experiment"], "adaptive-entropy-scheduling")
        self.assertEqual(len(config["worlds"]), 8)
        self.assertEqual(set(config["arms"]), {"L", "M", "H", "A", "C", "MTX", "E"})

    def test_duplicate_world_and_mismatched_multi_slot_config_are_rejected(self) -> None:
        duplicate = small_config()
        duplicate["worlds"].append({"seed": 41, "depth": 4})
        with self.assertRaisesRegex(ConfigError, "duplicate world seed"):
            validate_config(duplicate)

        bad_multi = small_config()
        bad_multi["arms"] = {
            "MTX": {"kind": "multi", "temperatures": [0.2, 0.7, 0.7, 1.2]}
        }
        with self.assertRaisesRegex(ConfigError, "candidate slot"):
            validate_config(bad_multi)

    def test_validation_returns_a_detached_json_native_copy(self) -> None:
        config = small_config()
        validated = validate_config(config)
        config["worlds"][0]["seed"] = 999

        self.assertEqual(validated["worlds"][0]["seed"], 41)
        json.dumps(validated, allow_nan=False)

    def test_e2_config_is_explicit_and_thresholds_are_validated(self) -> None:
        validated = validate_config(e2_config())
        self.assertEqual(
            validated["arms"]["E2"]["controller_version"],
            "validity-novelty-v2",
        )

        invalid_configs = []
        unknown = e2_config()
        unknown["arms"]["E2"]["controller_version"] = "unknown-controller"
        invalid_configs.append(unknown)
        missing = e2_config()
        del missing["arms"]["E2"]["minimum_valid_candidates"]
        invalid_configs.append(missing)
        too_many_valid = e2_config()
        too_many_valid["arms"]["E2"]["minimum_valid_candidates"] = 3
        invalid_configs.append(too_many_valid)
        too_many_useful = e2_config()
        too_many_useful["arms"]["E2"]["minimum_useful_new_behaviors"] = 3
        invalid_configs.append(too_many_useful)
        bad_tolerance = e2_config()
        bad_tolerance["arms"]["E2"]["useful_novelty_score_tolerance"] = 1.01
        invalid_configs.append(bad_tolerance)
        bad_precedence = e2_config()
        bad_precedence["arms"]["E2"]["decision_precedence"].reverse()
        invalid_configs.append(bad_precedence)
        ignored_e2_field = small_config()
        ignored_e2_field["arms"]["E"]["minimum_valid_candidates"] = 2
        invalid_configs.append(ignored_e2_field)

        for config in invalid_configs:
            adaptive_arm = config["arms"].get("E2", config["arms"].get("E"))
            with self.subTest(arm=adaptive_arm):
                with self.assertRaises(ConfigError):
                    validate_config(config)

    def test_frozen_confirmatory_config_rejects_protocol_drift(self) -> None:
        frozen = frozen_confirmatory_config()
        self.assertEqual(validate_config(frozen)["status"], "confirmatory-frozen")

        drifts: list[tuple[str, dict]] = []
        budget_drift = json.loads(json.dumps(frozen))
        budget_drift["episode"]["max_output_tokens"] = 127
        drifts.append(("budget", budget_drift))
        arm_drift = json.loads(json.dumps(frozen))
        arm_drift["arms"]["A"]["temperatures"][1] = 0.9
        drifts.append(("arm", arm_drift))
        missing_arm = json.loads(json.dumps(frozen))
        del missing_arm["arms"]["MTX"]
        drifts.append(("arm set", missing_arm))
        world_count_drift = json.loads(json.dumps(frozen))
        world_count_drift["worlds"].pop()
        drifts.append(("world count", world_count_drift))
        overlapping_seed = json.loads(json.dumps(frozen))
        overlapping_seed["worlds"][0]["seed"] = 1000
        drifts.append(("development seed overlap", overlapping_seed))
        missing_comparator = json.loads(json.dumps(frozen))
        del missing_comparator["primary_comparator"]
        drifts.append(("primary comparator", missing_comparator))
        missing_development_hash = json.loads(json.dumps(frozen))
        missing_development_hash["development_results_hash"] = ""
        drifts.append(("development hash", missing_development_hash))
        identity_drift = json.loads(json.dumps(frozen))
        identity_drift["model"]["snapshot"] = None
        drifts.append(("model identity", identity_drift))

        for label, drift in drifts:
            with self.subTest(label=label):
                with self.assertRaises(ConfigError):
                    validate_config(drift)


class ExperimentOrchestrationTests(unittest.TestCase):
    def test_e2_persists_only_a_sanitized_scalar_controller_trace(self) -> None:
        factory = RecordingFactory()
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            summary = run_experiment(e2_config(), factory)

        run = summary["runs"][0]
        self.assertEqual(run["arm_id"], "E2")
        trace = run["controller_trace"]
        self.assertEqual(len(trace), 2)
        self.assertEqual(
            [record["decision_reason"] for record in trace],
            ["probe_improved", "probe_ceiling"],
        )
        self.assertEqual(run["temperature_trajectory"], [1.0, 0.8])
        expected_fields = {
            "controller_version",
            "round_index",
            "round_best",
            "best_score",
            "pre_round_best_score",
            "improved",
            "planned_candidate_count",
            "valid_candidate_count",
            "new_behavior_count",
            "useful_new_behavior_count",
            "decision",
            "decision_reason",
            "previous_temperature",
            "next_temperature",
        }
        for record in trace:
            self.assertEqual(set(record), expected_fields)
            self.assertTrue(
                all(type(value) in {str, int, float, bool} for value in record.values())
            )
        encoded = json.dumps(trace, sort_keys=True)
        for forbidden in (
            HIDDEN_LAW,
            str(PRIVATE_TEST_LABEL),
            "candidate_expression",
            '"candidate":',
            "behavior_hash",
            "canonical_hash",
            "counterexample",
            "prediction",
            "test",
        ):
            self.assertNotIn(forbidden, encoded)

        e1_factory = RecordingFactory()
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            e1_summary = run_experiment(
                {**small_config(), "arms": {"E": small_config()["arms"]["E"]}},
                e1_factory,
            )
        self.assertNotIn("controller_trace", e1_summary["runs"][0])
        self.assertNotEqual(summary["config_hash"], e1_summary["config_hash"])

    def test_metered_envelope_closes_usage_and_token_fairness_gate(self) -> None:
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            summary = run_experiment(small_config(), MeteredFactory())

        self.assertTrue(summary["evidence"])
        self.assertEqual(summary["evidence_scope"], "development")
        self.assertTrue(summary["budget"]["actual_usage_available"])
        self.assertEqual(summary["budget"]["actual_input_tokens"], 80)
        self.assertEqual(summary["budget"]["actual_output_tokens"], 16)
        self.assertEqual(summary["budget"]["actual_billed_tokens"], 96)
        self.assertEqual(summary["budget"]["provider_requests"], 8)
        self.assertEqual(summary["budget"]["retry_count"], 0)
        fairness = summary["budget"]["token_fairness"]
        self.assertTrue(fairness["available"])
        self.assertTrue(fairness["passed"])
        first_candidate = summary["runs"][0]["candidates"][0]
        self.assertNotIn("prompt", first_candidate)
        self.assertRegex(first_candidate["prompt_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(first_candidate["input_tokens"], 10)
        self.assertEqual(first_candidate["candidate_format"], "json_expression")
        self.assertEqual(summary["model"]["observed_response_models"], ["unit-test-snapshot"])
        self.assertEqual(summary["model"]["finish_reason_counts"], {"stop": 8})

    def test_durable_summary_discards_raw_prompt_and_invalid_assistant_text(self) -> None:
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            summary = run_experiment(small_config(), MalformedMeteredFactory())

        encoded = json.dumps(summary, sort_keys=True)
        self.assertNotIn(RAW_ASSISTANT_SECRET, encoded)
        for run in summary["runs"]:
            for candidate in run["candidates"]:
                self.assertNotIn("prompt", candidate)
                self.assertEqual(
                    candidate["candidate_expression"],
                    "__INVALID_CANDIDATE_EXPRESSION__",
                )

    def test_token_fairness_failure_forces_non_evidence(self) -> None:
        factory = MeteredFactory(output_tokens_by_run=(1, 10))
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            summary = run_experiment(small_config(), factory)

        self.assertFalse(summary["evidence"])
        self.assertFalse(summary["budget"]["token_fairness"]["passed"])
        self.assertIn("2%", summary["evidence_reason"])

    def test_response_model_mismatch_forces_non_evidence(self) -> None:
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            summary = run_experiment(small_config(), WrongModelFactory())

        self.assertFalse(summary["evidence"])
        self.assertEqual(
            summary["model"]["observed_response_models"],
            ["unexpected-provider-alias"],
        )
        self.assertIn("does not exactly match", summary["evidence_reason"])

    def test_world_arm_grid_is_budgeted_serializable_and_private(self) -> None:
        factory = RecordingFactory()
        world = secret_world()
        with mock.patch("src.experiment.generate_world", return_value=world) as generate:
            summary = run_experiment(small_config(), factory)

        generate.assert_called_once_with(41, depth=3)
        self.assertFalse(summary["evidence"])
        self.assertIn("actual token usage", summary["evidence_reason"])
        self.assertEqual(summary["budget"]["run_count"], 2)
        self.assertEqual(summary["budget"]["generation_calls_planned"], 8)
        self.assertEqual(summary["budget"]["generation_calls_completed"], 8)
        self.assertEqual(summary["budget"]["max_output_tokens_planned"], 8 * 17)
        self.assertEqual(summary["budget"]["probe_point_evaluations_planned"], 16)
        self.assertEqual(len(summary["arm_hashes"]), 2)
        self.assertEqual(summary["world_hashes"], ["f" * 64])
        self.assertEqual(len(factory.contexts), 2)
        self.assertTrue(all(not hasattr(context, "world") for context in factory.contexts))
        self.assertTrue(
            all(
                "law" not in context.to_dict()
                and "test" not in context.to_dict()
                and "world_seed" not in context.to_dict()
                and "world_depth" not in context.to_dict()
                and "world_hash" not in context.to_dict()
                and "arm_id" not in context.to_dict()
                and "arm_hash" not in context.to_dict()
                and "arm_config" not in context.to_dict()
                and "run_id" not in context.to_dict()
                and "config_hash" not in context.to_dict()
                for context in factory.contexts
            )
        )

        calls = [call for generator in factory.generators for call in generator.calls]
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(call["max_output_tokens"] == 17 for call in calls))
        self.assertEqual(
            [call["seed"] for call in factory.generators[0].calls],
            [call["seed"] for call in factory.generators[1].calls],
        )
        generator_view = repr(calls)
        self.assertNotIn(HIDDEN_LAW, generator_view)
        self.assertNotIn(str(PRIVATE_TEST_LABEL), generator_view)
        self.assertNotIn("[98, 97, 96]", generator_view)

        runs = {run["arm_id"]: run for run in summary["runs"]}
        self.assertEqual(runs["L"]["temperature_trajectory"], [0.2, 0.2])
        self.assertEqual(runs["E"]["temperature_trajectory"], [1.0, 0.8])
        for run in runs.values():
            self.assertEqual(run["probe"]["final_selected_accuracy"], 1.0)
            self.assertEqual(run["final_test"]["accuracy"], 0.0)
            self.assertEqual(run["failure_counts"]["invalid_candidates"], 0)
            self.assertEqual(run["budget"]["max_output_tokens_per_call"], 17)
            self.assertIs(run["budget"]["actual_usage_available"], False)
            self.assertEqual(len(run["candidates"]), 4)
            self.assertNotIn("raw_response", run["candidates"][0])
            self.assertNotIn("predictions", run["candidates"][0])
            self.assertIn("failure_codes", run["candidates"][0])
            self.assertIn("by_code", run["failure_counts"])

        encoded = json.dumps(summary, allow_nan=False, sort_keys=True)
        self.assertNotIn(HIDDEN_LAW, encoded)
        self.assertNotIn(str(PRIVATE_TEST_LABEL), encoded)

    def test_each_world_arm_pair_receives_a_fresh_generator(self) -> None:
        config = small_config()
        config["worlds"].append({"seed": 42, "depth": 4})
        factory = RecordingFactory()
        second_world = SecretWorld(
            seed=42,
            depth_tier=4,
            world_hash="e" * 64,
            law=HIDDEN_LAW,
            train=secret_world().train,
            probe=secret_world().probe,
            test=secret_world().test,
        )
        with mock.patch(
            "src.experiment.generate_world", side_effect=[secret_world(), second_world]
        ):
            summary = run_experiment(config, factory)

        self.assertEqual(len(factory.generators), 4)
        self.assertEqual(len(summary["runs"]), 4)
        self.assertEqual(
            [run["arm_id"] for run in summary["runs"]],
            ["L", "E", "E", "L"],
        )
        self.assertEqual(
            [world["arm_execution_order"] for world in summary["worlds"]],
            [["L", "E"], ["E", "L"]],
        )
        self.assertEqual(
            {run["sampling_base_seed"] for run in summary["runs"]},
            {SAMPLING_BASE_SEED},
        )
        seed_sequences = [
            [call["seed"] for call in generator.calls]
            for generator in factory.generators
        ]
        self.assertTrue(all(item == seed_sequences[0] for item in seed_sequences))
        self.assertEqual(len({run["sampling_base_seed"] for run in summary["runs"]}), 1)
        adaptive = [
            run["temperature_trajectory"]
            for run in summary["runs"]
            if run["arm_id"] == "E"
        ]
        self.assertEqual(adaptive, [[1.0, 0.8], [1.0, 0.8]])

    def test_private_tests_are_delayed_until_all_model_calls_finish(self) -> None:
        events: list[str] = []

        class EventGenerator(RecordingGenerator):
            def generate(self, *args, **kwargs):
                events.append("generate")
                return super().generate(*args, **kwargs)

        class EventFactory:
            evidence = False

            def __call__(self, context: GeneratorContext) -> EventGenerator:
                del context
                return EventGenerator()

        def record_finalization(result, *, verifier=None):
            events.append("test")
            return finalize_episode_test(result, verifier=verifier)

        with (
            mock.patch("src.experiment.generate_world", return_value=secret_world()),
            mock.patch(
                "src.experiment.evaluate_episode_test",
                side_effect=record_finalization,
            ),
        ):
            run_experiment(small_config(), EventFactory())

        self.assertEqual(events, ["generate"] * 8 + ["test"] * 2)

    def test_frozen_full_arm_order_is_cyclically_counterbalanced(self) -> None:
        self.assertEqual(
            ARM_EXECUTION_BASE_ORDER,
            ("L", "H", "M", "MTX", "E", "A", "C"),
        )
        arms = load_config(ROOT / "configs" / "pilot.json")["arms"]
        orders = [_arm_execution_order(arms, index) for index in range(7)]
        for position in range(7):
            self.assertEqual({order[position] for order in orders}, set(arms))

        e2_only = {"L": {}, "MTX": {}, "E2": {}}
        self.assertEqual(
            _arm_execution_order(e2_only, 0),
            ("L", "MTX", "E2"),
        )
        e1_and_e2 = {"E": {}, "E2": {}}
        self.assertEqual(_arm_execution_order(e1_and_e2, 0), ("E", "E2"))

    def test_evidence_defaults_false_and_requires_complete_model_identity(self) -> None:
        config = small_config()
        config["arms"] = {"L": {"kind": "fixed", "temperature": 0.2}}
        config["episode"]["rounds"] = 1
        config["episode"]["candidates_per_round"] = 1

        class UnmarkedFactory:
            def __call__(self, context: GeneratorContext) -> RecordingGenerator:
                return RecordingGenerator()

        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            unmarked = run_experiment(config, UnmarkedFactory())
        self.assertIs(unmarked["evidence"], False)
        self.assertEqual(unmarked["evidence_scope"], "non-evidence")
        self.assertIn("explicitly", unmarked["evidence_reason"])

        incomplete = small_config()
        incomplete["arms"] = {"L": {"kind": "fixed", "temperature": 0.2}}
        incomplete["episode"]["rounds"] = 1
        incomplete["episode"]["candidates_per_round"] = 1
        incomplete["model"]["snapshot"] = None
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            missing_model = run_experiment(incomplete, RecordingFactory())
        self.assertIs(missing_model["evidence"], False)
        self.assertIn("snapshot", missing_model["evidence_reason"])

    def test_offline_smoke_factory_forces_non_evidence_marker(self) -> None:
        config = small_config()
        config["arms"] = {"L": {"kind": "fixed", "temperature": 0.2}}
        config["episode"]["rounds"] = 1
        config["episode"]["candidates_per_round"] = 1
        with mock.patch("src.experiment.generate_world", return_value=secret_world()):
            summary = run_experiment(config, OfflineSmokeGeneratorFactory())

        self.assertIs(summary["evidence"], False)
        self.assertEqual(summary["mode"], "offline-smoke")
        self.assertIn("plumbing", summary["evidence_reason"])
        json.dumps(summary, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
