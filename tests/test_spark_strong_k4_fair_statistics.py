from __future__ import annotations

import unittest
from fractions import Fraction
from unittest import mock

from src import spark_strong_k4_benchmark as benchmark


ROUTES = ("deepseek-flash", "deepseek-pro", "glm-5.2")


def _fraction_payload(value: Fraction) -> dict[str, object]:
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "value": float(value),
    }


def _route_score(
    route_id: str,
    *,
    mode: str = "positive",
    factual_strong_count: int | None = None,
) -> dict[str, object]:
    if mode == "positive":
        left = [True] * 6 + [False] * 26
        right = [False] * 32
        uniform_tail = Fraction(1, 100)
        uniform_passed = True
        default_strong_count = 6
        breadth = {
            "construction_stratum_count": 2,
            "unique_child_behavior_count": 2,
            "nonconstant_child_hit_count": 1,
        }
    elif mode == "holm_borderline":
        # Five favorable and zero adverse discordances gives raw p=1/32.
        # It is below .05, but three equal route p-values must all fail Holm.
        left = [True] * 5 + [False] * 27
        right = [False] * 32
        uniform_tail = Fraction(1, 100)
        uniform_passed = True
        default_strong_count = 5
        breadth = {
            "construction_stratum_count": 2,
            "unique_child_behavior_count": 2,
            "nonconstant_child_hit_count": 1,
        }
    elif mode == "strong_but_failed":
        left = [False] * 32
        right = [False] * 32
        uniform_tail = Fraction(1, 1)
        uniform_passed = False
        default_strong_count = 1
        breadth = {
            "construction_stratum_count": 1,
            "unique_child_behavior_count": 1,
            "nonconstant_child_hit_count": 0,
        }
    elif mode == "no_strong_hit":
        left = [False] * 32
        right = [False] * 32
        uniform_tail = Fraction(1, 1)
        uniform_passed = False
        default_strong_count = 0
        breadth = {
            "construction_stratum_count": 0,
            "unique_child_behavior_count": 0,
            "nonconstant_child_hit_count": 0,
        }
    else:
        raise AssertionError(f"unknown synthetic route mode: {mode}")

    strong_count = (
        default_strong_count
        if factual_strong_count is None
        else factual_strong_count
    )
    paired = benchmark.exact_one_sided_mcnemar(left, right)
    shortcut = benchmark.exact_one_sided_mcnemar(left, right)
    score: dict[str, object] = {
        "schema_version": 1,
        "kind": "spark-strong-k4-fair-choice-offline-score",
        "protocol_id": "spark-strong-k4-fair-choice-v1",
        "model_id": route_id,
        "public_manifest_sha256": "a" * 64,
        "private_key_sha256": "b" * 64,
        "current_source_manifest_sha256": "c" * 64,
        "received_response_count": 64,
        "invalid_response_count": 0,
        "pair_primary_U": paired,
        "factual_strong_F_count": strong_count,
        "factual_strong_breadth": breadth,
        "versus_frozen_B_star": {
            "B_star_policy_id": "synthetic-B-star",
            "B_star_factual_F_count": 0,
            "paired_test_model_greater_B_star": shortcut,
        },
        "versus_uniform_choice": {
            "observed_factual_F_count": strong_count,
            "exact_Poisson_binomial_upper_tail": _fraction_payload(uniform_tail),
            "critical_factual_F_count": 5,
            "exceeds_or_meets_critical_value": uniform_passed,
        },
    }
    score["score_sha256"] = benchmark._sha256_json(score)
    return score


def _minimal_scoring_fixture() -> tuple[dict[str, object], dict[str, object]]:
    factual_task = "TASK-FACTUAL"
    sham_task = "TASK-SHAM"
    public_manifest: dict[str, object] = {
        "public_manifest_sha256": "a" * 64,
        "current_source_manifest_sha256": "c" * 64,
        "tasks": [
            {"task_id": factual_task},
            {"task_id": sham_task},
        ],
    }
    private_key: dict[str, object] = {
        "private_key_sha256": "b" * 64,
        "pairs": [
            {
                "pair_id": "PAIR-SYNTHETIC",
                "option_to_raw_action": {"QVALID000": 0},
                "world_binding": {"construction_stratum": "affine_commutative"},
                "arms": {
                    "factual": {"task_id": factual_task, "actions": []},
                    "sham": {"task_id": sham_task, "actions": []},
                },
            }
        ],
        "baseline_report": {
            "B_star_policy_id": "synthetic-B-star",
            "B_star_factual_F_count": 0,
            "B_star_factual_F_by_pair": [False],
            "uniform_choice": {
                "factual_qualifying_action_counts": [1],
                "critical_factual_F_count": 1,
            },
        },
    }
    return public_manifest, private_key


class ExactMcNemarTests(unittest.TestCase):
    def test_zero_discordance_has_exact_one_tail(self) -> None:
        result = benchmark.exact_one_sided_mcnemar(
            [True, False, True],
            [True, False, True],
        )

        self.assertEqual(result["left_only_count"], 0)
        self.assertEqual(result["right_only_count"], 0)
        self.assertEqual(result["discordant_pair_count"], 0)
        self.assertEqual(
            result["exact_one_sided_p_value"],
            _fraction_payload(Fraction(1, 1)),
        )

    def test_hand_computed_three_vs_one_discordance_tail(self) -> None:
        result = benchmark.exact_one_sided_mcnemar(
            [True, True, True, False],
            [False, False, False, True],
        )

        # P[Binomial(4, 1/2) >= 3] = (4 + 1) / 16 = 5/16.
        self.assertEqual(result["left_only_count"], 3)
        self.assertEqual(result["right_only_count"], 1)
        self.assertEqual(
            result["exact_one_sided_p_value"],
            _fraction_payload(Fraction(5, 16)),
        )

    def test_five_favorable_discordances_are_one_over_thirty_two(self) -> None:
        result = benchmark.exact_one_sided_mcnemar(
            [True] * 5,
            [False] * 5,
        )

        self.assertEqual(
            result["exact_one_sided_p_value"],
            _fraction_payload(Fraction(1, 32)),
        )


class PoissonBinomialTests(unittest.TestCase):
    def test_two_nonidentical_probabilities_match_hand_distribution(self) -> None:
        # Success probabilities are 1/4 and 2/4.  The masses for 0, 1, 2
        # successes are 3/8, 1/2, and 1/8 respectively.
        counts = [1, 2]
        self.assertEqual(
            benchmark.poisson_binomial_tail(counts, 1, choice_count=4),
            Fraction(5, 8),
        )
        self.assertEqual(
            benchmark.poisson_binomial_tail(counts, 2, choice_count=4),
            Fraction(1, 8),
        )
        self.assertEqual(
            benchmark.poisson_binomial_tail(counts, 3, choice_count=4),
            Fraction(0, 1),
        )

    def test_critical_value_is_smallest_tail_at_or_below_alpha(self) -> None:
        counts = [1, 2]
        self.assertEqual(
            benchmark.poisson_binomial_critical_value(
                counts,
                alpha=Fraction(1, 5),
                choice_count=4,
            ),
            2,
        )
        # At alpha=1/10 even two successes has tail 1/8, so the frozen
        # convention returns n+1 to mark an unattainable critical value.
        self.assertEqual(
            benchmark.poisson_binomial_critical_value(
                counts,
                alpha=Fraction(1, 10),
                choice_count=4,
            ),
            3,
        )


class HolmAndJointClassificationTests(unittest.TestCase):
    def test_holm_step_down_values_and_boundary(self) -> None:
        result = benchmark.holm_adjusted_route_decisions(
            {
                "deepseek-flash": Fraction(1, 100),
                "deepseek-pro": Fraction(1, 40),
                "glm-5.2": Fraction(1, 10),
            },
            alpha=Fraction(1, 20),
        )

        self.assertEqual(
            result["deepseek-flash"],
            {
                "raw_p_value": Fraction(1, 100),
                "adjusted_p_value": Fraction(3, 100),
                "rejected": True,
            },
        )
        self.assertEqual(
            result["deepseek-pro"],
            {
                "raw_p_value": Fraction(1, 40),
                "adjusted_p_value": Fraction(1, 20),
                "rejected": True,
            },
        )
        self.assertEqual(
            result["glm-5.2"],
            {
                "raw_p_value": Fraction(1, 10),
                "adjusted_p_value": Fraction(1, 10),
                "rejected": False,
            },
        )

    def test_joint_classifier_uses_closed_route_set_and_holm(self) -> None:
        def positive_for(route: str) -> dict[str, object]:
            return _route_score(route, mode="positive")

        def failed_for(route: str) -> dict[str, object]:
            return _route_score(route, mode="strong_but_failed")

        cases = (
            (
                {route: positive_for(route) for route in ROUTES},
                "all_routes_effect_observed",
                set(ROUTES),
            ),
            (
                {
                    "deepseek-flash": positive_for("deepseek-flash"),
                    "deepseek-pro": failed_for("deepseek-pro"),
                    "glm-5.2": positive_for("glm-5.2"),
                },
                "cross_family_effect_observed",
                {"deepseek-flash", "glm-5.2"},
            ),
            (
                {
                    "deepseek-flash": positive_for("deepseek-flash"),
                    "deepseek-pro": positive_for("deepseek-pro"),
                    "glm-5.2": failed_for("glm-5.2"),
                },
                "deepseek_family_only_effect_observed",
                {"deepseek-flash", "deepseek-pro"},
            ),
            (
                {
                    "deepseek-flash": failed_for("deepseek-flash"),
                    "deepseek-pro": failed_for("deepseek-pro"),
                    "glm-5.2": positive_for("glm-5.2"),
                },
                "single_route_effect_observed",
                {"glm-5.2"},
            ),
            (
                {route: failed_for(route) for route in ROUTES},
                "effect_not_observed_under_frozen_protocol",
                set(),
            ),
        )
        for scores, expected_joint, expected_positive in cases:
            with self.subTest(expected_joint=expected_joint):
                report = benchmark.classify_joint_routes(scores)
                self.assertEqual(report["joint_classification"], expected_joint)
                self.assertEqual(set(report), {
                    "joint_classification",
                    "route_classifications",
                    "holm",
                })
                observed_positive = {
                    route
                    for route, row in report["route_classifications"].items()
                    if row["positive"]
                }
                self.assertEqual(observed_positive, expected_positive)
                for route, row in report["route_classifications"].items():
                    expected_route_label = (
                        "paired_strong_K4_effect_observed"
                        if route in expected_positive
                        else "strong_hits_shortcut_compatible"
                    )
                    self.assertEqual(row["classification"], expected_route_label)

    def test_unadjusted_point_zero_three_one_two_does_not_pass_holm(self) -> None:
        report = benchmark.classify_joint_routes(
            {
                route: _route_score(route, mode="holm_borderline")
                for route in ROUTES
            }
        )

        self.assertEqual(
            report["joint_classification"],
            "effect_not_observed_under_frozen_protocol",
        )
        self.assertFalse(
            any(
                row["positive"]
                for row in report["route_classifications"].values()
            )
        )
        for route in ROUTES:
            self.assertEqual(
                report["holm"][route]["adjusted_p_value"],
                Fraction(3, 32),
            )

    def test_missing_route_is_jointly_non_evaluable(self) -> None:
        report = benchmark.classify_joint_routes(
            {
                "deepseek-flash": _route_score(
                    "deepseek-flash", mode="positive"
                ),
                "deepseek-pro": _route_score("deepseek-pro", mode="positive"),
            }
        )

        self.assertEqual(
            report["joint_classification"],
            "non_evaluable_incomplete_attempt",
        )


class StrictResponseContractTests(unittest.TestCase):
    def test_extra_key_and_bare_option_id_are_received_invalid_misses(self) -> None:
        public_manifest, private_key = _minimal_scoring_fixture()
        responses = {
            "TASK-FACTUAL": {"expression": "QVALID000", "extra": "not allowed"},
            "TASK-SHAM": "QVALID000",
        }

        with mock.patch.object(benchmark, "validate_private_key"):
            result = benchmark.score_model_responses(
                public_manifest,
                private_key,
                responses,
                model_id="synthetic-route",
            )

        self.assertEqual(result["invalid_response_count"], 2)
        self.assertTrue(result["invalid_received_response_is_miss"])
        self.assertFalse(result["pairs"][0]["U_factual"])
        self.assertFalse(result["pairs"][0]["U_sham"])
        self.assertFalse(result["pairs"][0]["F_factual"])

    def test_missing_received_task_is_rejected_before_scoring(self) -> None:
        public_manifest, private_key = _minimal_scoring_fixture()
        responses = {
            "TASK-FACTUAL": {"expression": "QVALID000"},
        }

        with mock.patch.object(benchmark, "validate_private_key"):
            with self.assertRaisesRegex(
                benchmark.FairChoiceError,
                "exactly one received response per task",
            ):
                benchmark.score_model_responses(
                    public_manifest,
                    private_key,
                    responses,
                    model_id="synthetic-route",
                )


if __name__ == "__main__":
    unittest.main()
