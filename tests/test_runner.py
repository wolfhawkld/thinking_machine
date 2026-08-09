from __future__ import annotations

from dataclasses import dataclass
import json
import unittest
from unittest import mock

from src.prompts import build_round_prompt
from src.runner import (
    AdaptiveTemperaturePolicy,
    Archive,
    CANDIDATE_FORMATS,
    CandidateRecord,
    DEFAULT_MAX_OUTPUT_TOKENS,
    FixedTemperaturePolicy,
    GenerationResponse,
    SmokeTestGenerator,
    VerificationResult,
    _contains_equivalent,
    _slot_prompt_archive,
    run_episode,
    run_smoke_episode,
)
from src.verifier import Verifier
from src.dsl import to_sexpr
from src.policies import (
    AdaptiveTemperaturePolicy as CanonicalAdaptivePolicy,
    ValidityNoveltyAdaptiveTemperaturePolicy,
)


@dataclass(frozen=True)
class Example:
    point: tuple[int, int, int]
    label: int


@dataclass(frozen=True)
class World:
    train: tuple[Example, ...]
    probe: tuple[Example, ...]
    test: tuple[Example, ...]


TARGET = "(add (mul (var x1) (var x2)) (var x3))"


def _world() -> World:
    law = lambda p: p[0] * p[1] + p[2]
    points = ((1, 2, 3), (2, -1, 1), (-2, 2, 0), (0, 1, -2), (9, 9, 99))
    examples = tuple(Example(point, law(point)) for point in points)
    return World(train=examples[:2], probe=examples[2:4], test=examples[4:])


class FourSlotPolicy:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def temperatures_for_round(self, round_index: int, state: dict) -> tuple[float, ...]:
        return (0.1, 0.2, 0.3, 0.4)

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FeedbackVerifier:
    def __init__(self) -> None:
        self.splits: list[str] = []

    def verify_probe(self, candidate, points=None, **kwargs):
        self.splits.append("probe")
        # Deliberately return more feedback than the protocol permits.
        failures = tuple(
            {"inputs": (i, i, i), "expected": i, "predicted": i + 1}
            for i in range(5)
        )
        return VerificationResult(score=0.5, valid=True, counterexamples=failures)

    def verify_test(self, candidate, points=None, **kwargs):
        self.splits.append("test")
        return VerificationResult(score=0.75, valid=True)


class BoundarySpyGenerator:
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
        seed: int | None,
        state: dict,
    ):
        self.calls.append(
            {
                "prompt": prompt,
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
                "round_index": round_index,
                "candidate_index": candidate_index,
                "seed": seed,
                "state": dict(state),
            }
        )
        return {"expression": TARGET}


class BoundarySpyPolicy:
    def __init__(self) -> None:
        self.temperature_states: list[dict] = []
        self.updates: list[dict] = []

    def temperatures_for_round(self, round_index: int, state: dict) -> tuple[float, ...]:
        self.temperature_states.append(dict(state))
        return (0.5,)

    def update(self, **kwargs):
        self.updates.append(dict(kwargs))
        return kwargs


class BudgetSpyGenerator:
    def __init__(self) -> None:
        self.max_tokens: list[int] = []

    def generate(self, prompt: str, *, temperature: float, max_output_tokens: int):
        self.max_tokens.append(max_output_tokens)
        return {"expression": TARGET}


class InternalTypeErrorGenerator:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, *, temperature: float, max_output_tokens: int):
        self.calls += 1
        raise TypeError("provider failed after request")


class InvalidResultVerifier:
    def verify_probe(self, candidate, points=None, **kwargs):
        return VerificationResult(score=1.0, valid=False)


class InternalTypeErrorVerifier:
    def __init__(self) -> None:
        self.calls = 0

    def verify_probe(self, candidate, points=None, **kwargs):
        self.calls += 1
        raise TypeError("verifier implementation failed")


class ScoredExpressionVerifier:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def verify_probe(self, candidate, points=None, **kwargs):
        del points, kwargs
        return VerificationResult(score=self.scores[to_sexpr(candidate)], valid=True)

    def verify_test(self, candidate, points=None, **kwargs):
        del candidate, points, kwargs
        return VerificationResult(score=0.987654321, valid=True)


class RunnerTests(unittest.TestCase):
    def test_prompt_contains_frozen_grammar_but_not_temperature(self) -> None:
        world = _world()
        prompt = build_round_prompt(world, round_index=0, temperature=1.234567)

        self.assertIn("(add E E)", prompt)
        self.assertIn("(ite P E E)", prompt)
        self.assertIn("maximum AST depth 5", prompt)
        self.assertIn("maximum AST node count 31", prompt)
        self.assertNotIn("1.234567", prompt)
        # Private test labels/points are not copied into the generation prompt.
        self.assertNotIn("99", prompt)
        self.assertTrue(prompt.endswith("Output exactly that one JSON object now."))
        self.assertIn('"expression" MUST be a non-empty JSON string', prompt)
        self.assertIn("MUST NOT be a JSON array or nested JSON AST", prompt)

    def test_candidate_format_is_optional_closed_and_propagated(self) -> None:
        legacy = GenerationResponse(
            expression=TARGET,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
        )
        self.assertIsNone(legacy.candidate_format)
        self.assertIsNone(
            CandidateRecord(
                candidate=TARGET,
                raw_response=None,
                round_index=0,
                candidate_index=0,
                temperature=0.2,
            ).candidate_format
        )

        for candidate_format in CANDIDATE_FORMATS:
            with self.subTest(candidate_format=candidate_format):
                response = GenerationResponse(
                    expression=TARGET,
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=1.0,
                    candidate_format=candidate_format,
                )
                self.assertEqual(response.candidate_format, candidate_format)

        with self.assertRaisesRegex(ValueError, "CANDIDATE_FORMATS"):
            GenerationResponse(
                expression=TARGET,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1.0,
                candidate_format="unbounded-provider-detail",
            )
        with self.assertRaisesRegex(ValueError, "CANDIDATE_FORMATS"):
            CandidateRecord(
                candidate=TARGET,
                raw_response=None,
                round_index=0,
                candidate_index=0,
                temperature=0.2,
                candidate_format="unbounded-provider-detail",
            )

        response = GenerationResponse(
            expression=TARGET,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
            candidate_format="json_expression",
        )
        result = run_episode(
            _world(),
            SmokeTestGenerator(script=(response,)),
            policy=FixedTemperaturePolicy(0.2),
            rounds=1,
            candidates_per_round=1,
            evaluate_test=False,
        )
        self.assertEqual(result.rounds[0][0].candidate_format, "json_expression")

    def test_archive_tuple_ast_is_rendered_as_a_dsl_expression(self) -> None:
        ast = ("add", ("var", "x1"), ("var", "x2"))
        record = CandidateRecord(
            candidate=ast,
            raw_response={"expression": "(add (var x1) (var x2))"},
            round_index=0,
            candidate_index=0,
            temperature=0.7,
            probe_score=0.75,
            node_count=3,
        )

        prompt = build_round_prompt(_world(), round_index=1, archive=(record,))

        self.assertIn("candidate=(add (var x1) (var x2))", prompt)
        self.assertNotIn('["add",["var","x1"],["var","x2"]]', prompt)

    def test_archive_non_ast_tuple_keeps_generic_stable_rendering(self) -> None:
        record = CandidateRecord(
            candidate=("opaque-record", {"rank": 2}),
            raw_response=None,
            round_index=0,
            candidate_index=0,
            temperature=0.7,
        )

        prompt = build_round_prompt(_world(), round_index=1, archive=(record,))

        self.assertIn('candidate=["opaque-record",{"rank":2}]', prompt)

    def test_smoke_episode_runs_exactly_five_by_four_and_has_private_test(self) -> None:
        result = run_smoke_episode()

        self.assertEqual(result.candidate_count, 20)
        self.assertEqual([len(items) for items in result.rounds], [4, 4, 4, 4, 4])
        self.assertEqual(len(result.temperatures), 5)
        self.assertEqual(len(result.slot_temperatures), 5)
        self.assertTrue(all(len(items) == 4 for items in result.slot_temperatures))
        self.assertLessEqual(len(result.archive), 4)
        self.assertIsNotNone(result.final_candidate)
        self.assertIsNotNone(result.final_test)
        self.assertEqual(result.final_test_score, 1.0)

    def test_runner_default_uses_the_amended_256_token_cap(self) -> None:
        generator = SmokeTestGenerator()

        run_episode(_world(), generator, rounds=1)

        self.assertEqual(DEFAULT_MAX_OUTPUT_TOKENS, 256)
        self.assertTrue(
            all(
                call["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS
                for call in generator.calls
            )
        )

    def test_archive_drops_invalid_and_applies_frozen_tiebreak(self) -> None:
        archive = Archive(capacity=4)
        invalid = CandidateRecord(
            candidate="bad",
            raw_response="bad",
            round_index=0,
            candidate_index=0,
            temperature=0.2,
            probe_score=1.0,
            syntax_valid=False,
            runtime_valid=False,
            canonical_hash="000",
        )
        self.assertFalse(archive.add(invalid))
        self.assertEqual(len(archive), 0)

        larger = CandidateRecord(
            candidate="larger",
            raw_response="larger",
            round_index=0,
            candidate_index=0,
            temperature=0.2,
            probe_score=0.5,
            node_count=8,
            canonical_hash="z",
            behavior_hash="larger-behavior",
        )
        smaller = CandidateRecord(
            candidate="smaller",
            raw_response="smaller",
            round_index=0,
            candidate_index=1,
            temperature=0.2,
            probe_score=0.5,
            node_count=3,
            canonical_hash="z",
            behavior_hash="smaller-behavior",
        )
        hash_low = CandidateRecord(
            candidate="hash-low",
            raw_response="hash-low",
            round_index=0,
            candidate_index=2,
            temperature=0.2,
            node_count=3,
            probe_score=0.5,
            canonical_hash="a",
            behavior_hash="hash-low-behavior",
        )
        self.assertTrue(archive.add(larger))
        self.assertTrue(archive.add(smaller))
        self.assertTrue(archive.add(hash_low))
        self.assertEqual(
            [item.candidate for item in archive.entries],
            ["hash-low", "smaller", "larger"],
        )

    def test_all_invalid_first_round_is_not_reported_as_progress(self) -> None:
        generator = SmokeTestGenerator(script=["not-an-expression"] * 4 + [TARGET] * 16)
        policy = AdaptiveTemperaturePolicy()
        result = run_episode(_world(), generator, policy=policy, rounds=5, candidates_per_round=4)

        self.assertTrue(all(not record.syntax_valid for record in result.rounds[0]))
        self.assertFalse(policy.history[0]["improved"])
        self.assertEqual(len(result.archive), 1)

    def test_e2_all_invalid_round_decreases_temperature(self) -> None:
        generator = SmokeTestGenerator(script=["not-an-expression"] * 4 + [TARGET] * 4)
        policy = ValidityNoveltyAdaptiveTemperaturePolicy()

        result = run_episode(
            _world(),
            generator,
            policy=policy,
            rounds=2,
            candidates_per_round=4,
        )

        self.assertEqual(result.temperatures, [1.0, 0.8])
        self.assertEqual(policy.history[0]["valid_candidate_count"], 0)
        self.assertEqual(policy.history[0]["new_behavior_count"], 0)
        self.assertEqual(policy.history[0]["decision_reason"], "low_validity")

    def test_e2_behavioral_novelty_is_deduplicated_and_quality_gated(self) -> None:
        first_round = (
            "(var x1)",
            "(add (var x1) (const 0))",
            "(var x2)",
            "(var x3)",
        )
        second_round = (
            "(sub (var x1) (const 0))",
            "(neg (var x2))",
            "(neg (var x3))",
            "(add (var x2) (const 0))",
        )
        scores = {
            "(var x1)": 0.5,
            "(add (const 0) (var x1))": 0.5,
            "(var x2)": 0.25,
            "(var x3)": 0.0,
            "(sub (var x1) (const 0))": 0.5,
            "(neg (var x2))": 5.0 / 12.0,
            "(neg (var x3))": 1.0 / 3.0,
            "(add (const 0) (var x2))": 0.25,
        }
        generator = SmokeTestGenerator(script=first_round + second_round)
        policy = ValidityNoveltyAdaptiveTemperaturePolicy()

        result = run_episode(
            _world(),
            generator,
            verifier=ScoredExpressionVerifier(scores),
            policy=policy,
            rounds=2,
            candidates_per_round=4,
        )

        self.assertEqual(result.temperatures, [1.0, 0.8])
        first, second = policy.history
        self.assertEqual(first["new_behavior_count"], 3)
        self.assertEqual(first["useful_new_behavior_count"], 2)
        self.assertEqual(first["decision_reason"], "probe_improved")
        self.assertEqual(second["new_behavior_count"], 2)
        self.assertEqual(second["useful_new_behavior_count"], 1)
        self.assertEqual(second["decision_reason"], "useful_novelty")
        self.assertEqual(
            set(generator.calls[4]["state"]),
            {"round", "best_probe_score", "improved"},
        )
        encoded_history = json.dumps(result.policy_history, sort_keys=True)
        for forbidden in (
            "candidate_expression",
            '"candidate":',
            "behavior_hash",
            "canonical_hash",
            "counterexample",
            "prediction",
            "test",
        ):
            self.assertNotIn(forbidden, encoded_history)

    def test_e2_fails_closed_when_valid_behavior_hash_is_unavailable(self) -> None:
        policy = ValidityNoveltyAdaptiveTemperaturePolicy()
        with mock.patch("src.runner._behavior_hash", return_value=""):
            with self.assertRaisesRegex(ValueError, "behavior hash"):
                run_episode(
                    _world(),
                    SmokeTestGenerator(),
                    policy=policy,
                    rounds=1,
                    candidates_per_round=4,
                )

    def test_reused_stateful_policy_is_reset_for_each_episode(self) -> None:
        policy = CanonicalAdaptivePolicy()
        first = run_episode(
            _world(), SmokeTestGenerator(), policy=policy, rounds=1
        )
        second = run_episode(
            _world(), SmokeTestGenerator(), policy=policy, rounds=1
        )

        self.assertEqual(first.temperatures, [1.0])
        self.assertEqual(second.temperatures, [1.0])
        self.assertEqual(len(policy.history), 1)

    def test_policy_can_assign_distinct_temperature_per_slot(self) -> None:
        generator = SmokeTestGenerator()
        result = run_episode(
            _world(), generator, policy=FourSlotPolicy(), rounds=1, candidates_per_round=4
        )

        self.assertEqual(result.slot_temperatures, [(0.1, 0.2, 0.3, 0.4)])
        self.assertEqual(
            [call["temperature"] for call in generator.calls], [0.1, 0.2, 0.3, 0.4]
        )

    def test_single_temperature_arm_uses_identical_prompts_for_four_slots(self) -> None:
        generator = SmokeTestGenerator()
        run_episode(
            _world(),
            generator,
            policy=FixedTemperaturePolicy(0.5),
            rounds=1,
            candidates_per_round=4,
        )

        prompts = [call["prompt"] for call in generator.calls]
        self.assertEqual(len(prompts), 4)
        self.assertEqual(len(set(prompts)), 1)

    def test_mtx_context_contains_local_best_without_other_local_only_best(self) -> None:
        def record(name: str, score: float, node_count: int) -> CandidateRecord:
            return CandidateRecord(
                candidate=name,
                raw_response=name,
                round_index=0,
                candidate_index=0,
                temperature=0.7,
                probe_score=score,
                node_count=node_count,
                canonical_hash=f"{name}-canonical",
                behavior_hash=f"{name}-behavior",
            )

        elite = record("global-elite", 1.0, 3)
        slot_a = record("slot-a-only", 0.4, 5)
        slot_b = record("slot-b-only", 0.3, 5)
        context_a = _slot_prompt_archive((elite,), slot_a, capacity=4)
        context_b = _slot_prompt_archive((elite,), slot_b, capacity=4)

        self.assertLessEqual(len(context_a), 4)
        self.assertLessEqual(len(context_b), 4)
        self.assertIn(slot_a, context_a)
        self.assertIn(elite, context_a)
        self.assertNotIn(slot_b, context_a)
        self.assertIn(slot_b, context_b)
        self.assertIn(elite, context_b)
        self.assertNotIn(slot_a, context_b)

    def test_generator_and_policy_never_receive_private_probe_records(self) -> None:
        generator = BoundarySpyGenerator()
        policy = BoundarySpyPolicy()
        result = run_episode(
            _world(),
            generator,
            verifier=FeedbackVerifier(),
            policy=policy,
            rounds=2,
            candidates_per_round=4,
        )

        self.assertEqual(len(generator.calls), 8)
        for call in generator.calls:
            state = call["state"]
            self.assertEqual(set(state), {"round", "best_probe_score", "improved"})
            self.assertNotIn("archive", state)
            self.assertNotIn("expected", repr(state))
        for state in policy.temperature_states:
            self.assertEqual(set(state), {"round", "best_probe_score", "improved"})
        for update in policy.updates:
            self.assertEqual(
                set(update), {"round_index", "round_best", "best_score", "improved"}
            )
            self.assertNotIn("expected", repr(update))

        # The second-round prompt contains the two deliberately released
        # counterexamples, but not the unreleased third failure.
        second_round_prompt = generator.calls[4]["prompt"]
        self.assertIn('"inputs":[0,0,0]', second_round_prompt)
        self.assertNotIn('"inputs":[2,2,2]', second_round_prompt)
        self.assertEqual(len(result.counterexamples), 4)

    def test_max_output_tokens_is_forwarded_once_and_recorded(self) -> None:
        generator = BudgetSpyGenerator()
        result = run_episode(
            _world(),
            generator,
            policy=FixedTemperaturePolicy(0.5),
            rounds=1,
            candidates_per_round=2,
            max_output_tokens=37,
        )

        self.assertEqual(generator.max_tokens, [37, 37])
        self.assertEqual(
            [record.max_output_tokens for record in result.rounds[0]], [37, 37]
        )

    def test_internal_generator_typeerror_is_not_retried(self) -> None:
        generator = InternalTypeErrorGenerator()
        with self.assertRaisesRegex(TypeError, "provider failed after request"):
            run_episode(
                _world(),
                generator,
                policy=FixedTemperaturePolicy(0.5),
                rounds=1,
                candidates_per_round=1,
            )
        self.assertEqual(generator.calls, 1)

    def test_internal_verifier_typeerror_is_not_retried(self) -> None:
        verifier = InternalTypeErrorVerifier()
        with self.assertRaisesRegex(TypeError, "verifier implementation failed"):
            run_episode(
                _world(),
                SmokeTestGenerator(),
                verifier=verifier,
                policy=FixedTemperaturePolicy(0.5),
                rounds=1,
                candidates_per_round=1,
            )
        self.assertEqual(verifier.calls, 1)

    def test_aggregate_invalid_verification_is_excluded_from_archive(self) -> None:
        result = run_episode(
            _world(),
            SmokeTestGenerator(),
            verifier=InvalidResultVerifier(),
            policy=FixedTemperaturePolicy(0.5),
            rounds=1,
            candidates_per_round=1,
        )

        self.assertEqual(len(result.archive), 0)
        self.assertFalse(result.rounds[0][0].runtime_valid)

    def test_structured_depth_failure_survives_the_episode_boundary(self) -> None:
        too_deep = "(neg (neg (neg (neg (neg (var x1))))))"
        result = run_episode(
            _world(),
            SmokeTestGenerator(expression=too_deep),
            verifier=Verifier(),
            policy=FixedTemperaturePolicy(0.5),
            rounds=1,
            candidates_per_round=1,
        )

        record = result.rounds[0][0]
        self.assertFalse(record.syntax_valid)
        self.assertIn("depth", record.failure_codes)
        self.assertEqual(len(result.archive), 0)

    def test_default_verifier_enforces_the_same_frozen_validation(self) -> None:
        too_deep = "(neg (neg (neg (neg (neg (var x1))))))"
        result = run_episode(
            _world(),
            SmokeTestGenerator(expression=too_deep),
            policy=FixedTemperaturePolicy(0.5),
            rounds=1,
            candidates_per_round=1,
        )

        self.assertIn("depth", result.rounds[0][0].failure_codes)
        self.assertEqual(len(result.archive), 0)

    def test_counterexample_dedup_ignores_prediction_changes(self) -> None:
        first = {"inputs": (1, 2, 3), "expected": 7, "predicted": 5}
        changed = {"inputs": (1, 2, 3), "expected": 7, "predicted": -99}
        different_label = {"inputs": (1, 2, 3), "expected": 8, "predicted": -99}

        self.assertTrue(_contains_equivalent((first,), changed))
        self.assertFalse(_contains_equivalent((first,), different_label))

    def test_feedback_is_capped_at_two_new_counterexamples_per_round_and_test_is_last(self) -> None:
        verifier = FeedbackVerifier()
        generator = SmokeTestGenerator()
        result = run_episode(
            _world(), generator, verifier=verifier, policy=AdaptiveTemperaturePolicy(), rounds=2
        )

        # The verifier offers five failures every round.  The protocol releases
        # at most two *new* failures per round; duplicate failures are ignored,
        # so two rounds expose four distinct counterexamples cumulatively.
        self.assertEqual(len(result.counterexamples), 4)
        self.assertEqual(verifier.splits[:-1], ["probe"] * 8)
        self.assertEqual(verifier.splits[-1], "test")
        self.assertEqual(result.final_test_score, 0.75)

    def test_counterexample_release_limit_is_configurable_per_round(self) -> None:
        result = run_episode(
            _world(),
            SmokeTestGenerator(),
            verifier=FeedbackVerifier(),
            policy=FixedTemperaturePolicy(0.5),
            rounds=3,
            candidates_per_round=1,
            max_counterexamples_per_round=1,
        )

        self.assertEqual(len(result.counterexamples), 3)


if __name__ == "__main__":
    unittest.main()
